import { useEffect, useId, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { getSessionGeneration, getToken, isAbort, sessionMatches } from '../../shared/api'
import { ArrowRight, Eye, EyeOff, FlaskConical, Mail, User } from 'lucide-react'
import { toast } from 'sonner'
import { useAuth } from '../../app/AuthProvider'
import { Modal } from '../../components/Modal'

export function AuthModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { login, register, sessionVersion } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', email: '' })
  const prefix = useId(), location = useLocation(), key = `${open}:${mode}:${location.key}`
  const latest = useRef(key); latest.current = key
  const mounted = useRef(false), sequence = useRef(0), inFlight = useRef(false), controller = useRef<AbortController | null>(null)
  const invalidate = () => { sequence.current++; controller.current?.abort() }
  const close = () => { invalidate(); onClose() }
  useEffect(() => {
    mounted.current = true; setBusy(false); inFlight.current = false; invalidate()
    return () => { mounted.current = false; invalidate() }
  }, [key])
  useEffect(() => { setBusy(false); inFlight.current = false; setForm({ username: '', password: '', email: '' }) }, [sessionVersion])
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (inFlight.current || !form.username.trim() || !form.password || (mode === 'register' && form.password.length < 8)) return
    if (mode === 'register' && form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
      toast.error('邮箱格式不正确')
      return
    }
    inFlight.current = true; setBusy(true)
    const request = ++sequence.current, generation = getSessionGeneration(), token = getToken()
    const abort = new AbortController(); controller.current = abort
    const local = () => mounted.current && !abort.signal.aborted && latest.current === key && request === sequence.current
    let succeeded = false
    try {
      const caller = { signal: abort.signal, current: local }
      mode === 'login' ? await login(form.username, form.password, caller) : await register(form.username, form.password, form.email || undefined, caller)
      succeeded = true
      // AuthProvider owns the guarded token transition; accept exactly our one new session.
      if (!local() || getSessionGeneration() !== generation + 1) return
      toast.success(mode === 'login' ? '欢迎回到知核' : '账号创建成功'); setForm({ username: '', password: '', email: '' }); close()
    } catch (error) { if (local() && sessionMatches(generation, token) && !isAbort(error)) toast.error((error as Error).message) }
    finally { if (local() && (succeeded ? getSessionGeneration() === generation + 1 : sessionMatches(generation, token))) { inFlight.current = false; setBusy(false) } }
  }

  // Detect if input looks like an email
  const isEmailInput = form.username.includes('@')

  return <Modal open={open} onClose={close} title={mode === 'login' ? '登录知核' : '创建账号'}>
    <div className="mb-6 rounded-2xl bg-ink p-4 text-white">
      <div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-mint text-ink"><FlaskConical size={20} /></span><div><p className="font-bold">科研，从这里更进一步</p><p className="text-xs text-white/55">学习 · 复现 · 分享</p></div></div>
    </div>
    <form onSubmit={submit} className="space-y-4">
      {mode === 'login' ? (
        <>
          <div>
            <label htmlFor={`${prefix}-username`} className="label">
              账号
              <span className="ml-2 text-xs font-normal text-slate-400">用户名或邮箱</span>
            </label>
            <div className="relative">
              <div className="pointer-events-none absolute left-3 top-3.5 text-slate-400">
                {isEmailInput ? <Mail size={17} /> : <User size={17} />}
              </div>
              <input
                id={`${prefix}-username`}
                className="input pl-10"
                required
                autoComplete="username"
                placeholder="请输入用户名或邮箱"
                value={form.username}
                onChange={e => setForm({ ...form, username: e.target.value })}
              />
            </div>
          </div>
          <div>
            <label htmlFor={`${prefix}-password`} className="label">密码</label>
            <div className="relative">
              <input
                id={`${prefix}-password`}
                className="input pr-11"
                required
                type={show ? 'text' : 'password'}
                autoComplete="current-password"
                placeholder="输入密码"
                value={form.password}
                onChange={e => setForm({ ...form, password: e.target.value })}
              />
              <button
                aria-label={show ? '隐藏密码' : '显示密码'}
                type="button"
                onClick={() => setShow(!show)}
                className="absolute right-3 top-3.5 text-slate-400 hover:text-slate-600"
              >
                {show ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </div>
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-500">
              {isEmailInput ? '📧 将使用邮箱登录' : '👤 将使用用户名登录'}
            </span>
            <button
              type="button"
              className="text-emerald-600 hover:text-emerald-700 font-medium"
              onClick={() => { invalidate(); setMode('register') }}
            >
              没有账号？立即注册
            </button>
          </div>
        </>
      ) : (
        <>
          <div>
            <label htmlFor={`${prefix}-username`} className="label">
              用户名
              <span className="ml-2 text-xs font-normal text-slate-400">将作为您的显示名称</span>
            </label>
            <div className="relative">
              <div className="pointer-events-none absolute left-3 top-3.5 text-slate-400">
                <User size={17} />
              </div>
              <input
                id={`${prefix}-username`}
                className="input pl-10"
                required
                autoComplete="username"
                placeholder="3-50位字母、数字或 _.-"
                value={form.username}
                onChange={e => setForm({ ...form, username: e.target.value })}
              />
            </div>
          </div>
          <div>
            <label htmlFor={`${prefix}-email`} className="label">
              邮箱
              <span className="ml-2 text-xs font-normal text-slate-400">选填，用于找回密码</span>
            </label>
            <div className="relative">
              <div className="pointer-events-none absolute left-3 top-3.5 text-slate-400">
                <Mail size={17} />
              </div>
              <input
                id={`${prefix}-email`}
                className="input pl-10"
                type="email"
                autoComplete="email"
                placeholder="example@email.com"
                value={form.email}
                onChange={e => setForm({ ...form, email: e.target.value })}
              />
            </div>
          </div>
          <div>
            <label htmlFor={`${prefix}-password`} className="label">密码</label>
            <div className="relative">
              <input
                id={`${prefix}-password`}
                className="input pr-11"
                required
                minLength={8}
                type={show ? 'text' : 'password'}
                autoComplete="new-password"
                placeholder="至少8位密码"
                value={form.password}
                onChange={e => setForm({ ...form, password: e.target.value })}
              />
              <button
                aria-label={show ? '隐藏密码' : '显示密码'}
                type="button"
                onClick={() => setShow(!show)}
                className="absolute right-3 top-3.5 text-slate-400 hover:text-slate-600"
              >
                {show ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </div>
          </div>
        </>
      )}
      <button disabled={busy} className="btn-mint w-full py-3.5">
        {busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并进入'} <ArrowRight size={17} />
      </button>
    </form>
    {mode === 'register' && (
      <button
        className="mt-5 w-full text-center text-sm text-slate-500 hover:text-ink"
        onClick={() => { invalidate(); setMode('login') }}
      >
        已有账号？返回登录
      </button>
    )}
  </Modal>
}
