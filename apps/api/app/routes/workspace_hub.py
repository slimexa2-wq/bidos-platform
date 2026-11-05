"""Routes are configured by the composition root; no database on import."""
from fastapi import APIRouter, HTTPException, Request
from app.services.workspace_hub import WorkspaceHub, WorkConflict

router = APIRouter(prefix='/api/workspace', tags=['工作目录'])
_service = None
_factory = None
_snapshots = None


def configure_snapshots(service):
    global _snapshots
    _snapshots = service


def configure(service):
    """Accept an instance or a lazy getter; resolve the getter once after startup."""
    global _service, _factory
    _service = None if callable(service) else service
    _factory = service if callable(service) else None


def call(method, *args):
    global _service
    try:
        if _service is None and _factory is not None:
            _service = _factory()
        if _service is None:
            raise HTTPException(status_code=503, detail='工作目录服务未初始化')
        return getattr(_service, method)(*args)
    except WorkConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get('')
def workspace(request: Request, refresh: bool = False):
    return _snapshots.response('workspace', request, refresh) if _snapshots else call('snapshot')


@router.get('/snapshot-status')
def snapshot_status():
    return _snapshots.status() if _snapshots else {'lastError': '快照未初始化'}


@router.get('/export-data')
def export_data():
    return call('export_data')


@router.get('/projects/{project_id}/work')
def get_work(project_id: str):
    return call('get_work', project_id)


@router.post('/projects/{project_id}/work')
def save_work(project_id: str, body: dict):
    return call('save_work', project_id, body)
