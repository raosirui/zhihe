import { useEffect, useId, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { getSessionGeneration, getToken, isAbort, sessionMatches } from '../lib'
import { ArrowRight, Eye, EyeOff, FlaskConical } from 'lucide-react'
import { toast } from 'sonner'
import { useAuth } from '../AuthContext'
import { Modal } from './Modal'

export function AuthModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { login, register, sessionVersion } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', nickname: '' })
  const prefix = useId(), location = useLocation(), key = `${open}:${mode}:${location.key}`
  const latest = useRef(key); latest.current = key
  const mounted = useRef(false), sequence = useRef(0), inFlight = useRef(false), controller = useRef<AbortController | null>(null)
  const invalidate = () => { sequence.current++; controller.current?.abort() }
  const close = () => { invalidate(); onClose() }
  useEffect(() => {
    mounted.current = true; setBusy(false); inFlight.current = false; invalidate()
    return () => { mounted.current = false; invalidate() }
  }, [key])
  useEffect(() => { setBusy(false); inFlight.current = false; setForm({ username: '', password: '', nickname: '' }) }, [sessionVersion])
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (inFlight.current || !form.username.trim() || !form.password || (mode === 'register' && form.password.length < 8)) return
    inFlight.current = true; setBusy(true)
    const request = ++sequence.current, generation = getSessionGeneration(), token = getToken()
    const abort = new AbortController(); controller.current = abort
    const local = () => mounted.current && !abort.signal.aborted && latest.current === key && request === sequence.current
    let succeeded = false
    try {
      const caller = { signal: abort.signal, current: local }
      mode === 'login' ? await login(form.username, form.password, caller) : await register(form.username, form.password, form.nickname, caller)
      succeeded = true
      // AuthProvider owns the guarded token transition; accept exactly our one new session.
      if (!local() || getSessionGeneration() !== generation + 1) return
      toast.success(mode === 'login' ? '欢迎回到知核' : '账号创建成功'); setForm({ username: '', password: '', nickname: '' }); close()
    } catch (error) { if (local() && sessionMatches(generation, token) && !isAbort(error)) toast.error((error as Error).message) }
    finally { if (local() && (succeeded ? getSessionGeneration() === generation + 1 : sessionMatches(generation, token))) { inFlight.current = false; setBusy(false) } }
  }
  return <Modal open={open} onClose={close} title={mode === 'login' ? '登录知核' : '创建账号'}>
    <div className="mb-6 rounded-2xl bg-ink p-4 text-white">
      <div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-mint text-ink"><FlaskConical size={20} /></span><div><p className="font-bold">科研，从这里更进一步</p><p className="text-xs text-white/55">学习 · 复现 · 分享</p></div></div>
    </div>
    <form onSubmit={submit} className="space-y-4">
      {mode === 'register' && <div><label htmlFor={`${prefix}-nickname`} className="label">昵称</label><input id={`${prefix}-nickname`} className="input" required placeholder="大家怎么称呼你" value={form.nickname} onChange={e => setForm({ ...form, nickname: e.target.value })} /></div>}
      <div><label htmlFor={`${prefix}-username`} className="label">账号</label><input id={`${prefix}-username`} className="input" required autoComplete="username" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></div>
      <div><label htmlFor={`${prefix}-password`} className="label">密码</label><div className="relative"><input id={`${prefix}-password`} className="input pr-11" required minLength={mode === 'register' ? 8 : undefined} type={show ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /><button aria-label={show ? '隐藏密码' : '显示密码'} type="button" onClick={() => setShow(!show)} className="absolute right-3 top-3.5 text-slate-400">{show ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></div>
      <button disabled={busy} className="btn-mint w-full py-3.5">{busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并进入'} <ArrowRight size={17} /></button>
    </form>
    <button className="mt-5 w-full text-center text-sm text-slate-500 hover:text-ink" onClick={() => { invalidate(); setMode(mode === 'login' ? 'register' : 'login') }}>{mode === 'login' ? '还没有账号？立即注册' : '已有账号？返回登录'}</button>
  </Modal>
}

