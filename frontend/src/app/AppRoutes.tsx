import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { HomePage, ToolkitDetailPage, ToolkitListPage } from '../features/catalog'
import { CheckoutPage } from '../features/commerce'
import { LearnPage } from '../features/learning'
import { AdminPage } from '../features/admin'
import { BatchPublishPage, PublishPage } from '../features/publishing'
import { FavoritesPage, MeDashboard, MeLayout, MyToolkitsPage, OrdersPage, WalletPage } from '../features/account'

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="toolkits" element={<ToolkitListPage />} />
        <Route path="toolkits/:id" element={<ToolkitDetailPage />} />
        <Route path="toolkits/:id/learn" element={<LearnPage />} />
        <Route path="checkout/:id" element={<CheckoutPage />} />
        <Route path="publish" element={<PublishPage />} />
        <Route path="publish/:id" element={<PublishPage />} />
        <Route path="publish/batch" element={<BatchPublishPage />} />
        <Route path="me" element={<MeLayout />}>
          <Route index element={<MeDashboard />} />
          <Route path="wallet" element={<WalletPage />} />
          <Route path="favorites" element={<FavoritesPage />} />
          <Route path="toolkits" element={<MyToolkitsPage />} />
          <Route path="orders" element={<OrdersPage />} />
        </Route>
        <Route path="admin" element={<AdminPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
