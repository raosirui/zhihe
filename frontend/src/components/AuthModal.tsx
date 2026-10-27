import { useState } from 'react'
import { ArrowRight, Eye, EyeOff, FlaskConical } from 'lucide-react'
import { toast } from 'sonner'
import { useAuth } from '../AuthContext'
import { Modal } from './Modal'

export function AuthModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', nickname: '' })
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true)
    try {
      mode === 'login' ? await login(form.username, form.password) : await register(form.username, form.password, form.nickname)
      toast.success(mode === 'login' ? '欢迎回到知核' : '账号创建成功')
      onClose()
    } catch (error) { toast.error((error as Error).message) }
    finally { setBusy(false) }
  }
  return <Modal open={open} onClose={onClose} title={mode === 'login' ? '登录知核' : '创建账号'}>
    <div className="mb-6 rounded-2xl bg-ink p-4 text-white">
      <div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-mint text-ink"><FlaskConical size={20} /></span><div><p className="font-bold">科研，从这里更进一步</p><p className="text-xs text-white/55">学习 · 复现 · 分享</p></div></div>
    </div>
    <form onSubmit={submit} className="space-y-4">
      {mode === 'register' && <div><label className="label">昵称</label><input className="input" required placeholder="大家怎么称呼你" value={form.nickname} onChange={e => setForm({ ...form, nickname: e.target.value })} /></div>}
      <div><label className="label">账号</label><input className="input" required autoComplete="username" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></div>
      <div><label className="label">密码</label><div className="relative"><input className="input pr-11" required minLength={8} type={show ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /><button type="button" onClick={() => setShow(!show)} className="absolute right-3 top-3.5 text-slate-400">{show ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></div>
      <button disabled={busy} className="btn-mint w-full py-3.5">{busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并进入'} <ArrowRight size={17} /></button>
    </form>
    <button className="mt-5 w-full text-center text-sm text-slate-500 hover:text-ink" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>{mode === 'login' ? '还没有账号？立即注册' : '已有账号？返回登录'}</button>
    {mode === 'login' && <p className="mt-5 rounded-xl bg-slate-50 p-3 text-center text-xs text-slate-400">演示账号 researcher · User@123</p>}
  </Modal>
}

