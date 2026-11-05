/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** mock: 前端本地数据；api: 调用 VITE_API_BASE */
  readonly VITE_DATA_MODE?: string
  /** 真实 API 前缀，例如 http://localhost:8000/api */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
