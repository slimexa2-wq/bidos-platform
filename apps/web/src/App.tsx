import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { HomePage } from './pages/HomePage'
import { BlindBidPage } from './pages/BlindBidPage'
import { RegistrationPage } from './pages/RegistrationPage'
import { EvaluationPage } from './pages/EvaluationPage'
import { DocumentPage } from './pages/DocumentPage'
import { ResultsPage } from './pages/ResultsPage'
import { CompetitorsPage } from './pages/CompetitorsPage'
import { SearchPage } from './pages/SearchPage'
import { AlertsPage } from './pages/AlertsPage'
import { BidBondsPage } from './pages/BidBondsPage'
import { OpsPage } from './pages/OpsPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { DecisionCard } from './pages/decision/DecisionCard'


/**
 * 路由表。
 *
 * 体检修复（2026-09-24）：
 * 1. 补齐 `path="*"` 兜底。此前未知路径下 react-router 连布局路由
 *    `<AppShell/>` 都不渲染 → 整页纯白无侧边栏，用户投诉"全是空白"。
 * 2. 为复数误写路径加 `Navigate` 别名。真实路由是单数（/registration），
 *    而用户很自然会写 /registrations；此前这些地址全部白屏。
 */
export default function App() {
  return (
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<HomePage />} />
            <Route path="blind" element={<BlindBidPage />} />
            <Route path="registration" element={<RegistrationPage />} />
            <Route path="evaluation" element={<EvaluationPage />} />
            <Route path="document" element={<DocumentPage />} />
            <Route path="decision-brief" element={<DecisionCard />} />

            <Route path="results" element={<ResultsPage />} />
            <Route path="bid-bonds" element={<BidBondsPage />} />
            <Route path="competitors" element={<CompetitorsPage />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="alerts" element={<AlertsPage />} />
            <Route path="ops" element={<OpsPage />} />
            {/* 常见复数误写 → 真实路由（永久重定向，避免白屏） */}
            <Route path="registrations" element={<Navigate to="/registration" replace />} />
            <Route path="evaluations" element={<Navigate to="/evaluation" replace />} />
            <Route path="documents" element={<Navigate to="/document" replace />} />
            <Route path="opportunities" element={<Navigate to="/blind" replace />} />
            <Route path="projects" element={<Navigate to="/blind" replace />} />
            <Route path="alerts/timeline" element={<Navigate to="/alerts" replace />} />
            {/* 兜底：未知路径必须渲染出页面，不能白屏 */}
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
  )
}
