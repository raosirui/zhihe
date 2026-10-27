import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { HomePage } from './pages/HomePage'
import { ToolkitListPage } from './pages/ToolkitListPage'
import { ToolkitDetailPage } from './pages/ToolkitDetailPage'
import { LearnPage } from './pages/LearnPage'
import { CheckoutPage } from './pages/CheckoutPage'
import { PublishPage } from './pages/PublishPage'
import { BatchPublishPage } from './pages/BatchPublishPage'
import { AdminPage } from './pages/AdminPage'
import { FavoritesPage, MeDashboard, MeLayout, MyToolkitsPage, OrdersPage, WalletPage } from './pages/MePages'

export default function App() {
  return <Routes><Route element={<Layout />}>
    <Route index element={<HomePage />} />
    <Route path="toolkits" element={<ToolkitListPage />} />
    <Route path="toolkits/:id" element={<ToolkitDetailPage />} />
    <Route path="toolkits/:id/learn" element={<LearnPage />} />
    <Route path="checkout/:id" element={<CheckoutPage />} />
    <Route path="publish" element={<PublishPage />} />
    <Route path="publish/batch" element={<BatchPublishPage />} />
    <Route path="me" element={<MeLayout />}><Route index element={<MeDashboard />} /><Route path="wallet" element={<WalletPage />} /><Route path="favorites" element={<FavoritesPage />} /><Route path="toolkits" element={<MyToolkitsPage />} /><Route path="orders" element={<OrdersPage />} /></Route>
    <Route path="admin" element={<AdminPage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Route></Routes>
}

