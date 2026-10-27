import { useEffect, useState } from 'react'
import { ArrowRight, BookOpenCheck, BrainCircuit, Dna, Search, ShieldCheck, Sparkles, UsersRound } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib'
import type { Toolkit } from '../types'
import { ToolkitCard } from '../components/ToolkitCard'

const categories = [
  { name: '生信分析', icon: BrainCircuit, color: 'bg-violet-100 text-violet-700', detail: '富集、差异与多组学分析' },
  { name: '单细胞', icon: Dna, color: 'bg-emerald-100 text-emerald-700', detail: '从质控到细胞类型注释' },
  { name: '结构生物学', icon: Sparkles, color: 'bg-sky-100 text-sky-700', detail: '结构预测与分子模拟' },
  { name: '基因编辑', icon: ShieldCheck, color: 'bg-amber-100 text-amber-700', detail: '靶点设计与脱靶评估' },
]

export function HomePage() {
  const [items, setItems] = useState<Toolkit[]>([]); const [keyword, setKeyword] = useState(''); const navigate = useNavigate()
  const [stats, setStats] = useState<{ toolkits: number; users: number }>({ toolkits: 0, users: 0 })
  useEffect(() => { api<{ items: Toolkit[] }>('/toolkits').then(x => setItems(x.items.slice(0, 6))).catch(() => {}) }, [])
  useEffect(() => { api<{ toolkits: number; users: number }>('/stats').then(setStats).catch(() => {}) }, [])
  const search = (e: React.FormEvent) => { e.preventDefault(); navigate(`/toolkits?q=${encodeURIComponent(keyword)}`) }
  return <>
    <section className="relative overflow-hidden bg-ink text-white">
      <div className="absolute -left-20 top-24 size-72 rounded-full bg-emerald-600/20 blur-3xl" /><div className="absolute right-0 top-0 size-[500px] rounded-full bg-lime-300/10 blur-3xl" />
      <div className="relative mx-auto grid max-w-[1440px] gap-12 px-4 py-16 sm:px-6 sm:py-24 lg:grid-cols-[1.1fr_.9fr] lg:items-center lg:px-8 lg:py-28">
        <div className="animate-enter">
          <span className="chip border border-mint/20 bg-mint/10 text-mint"><Sparkles size={13} />探索生物科研的新方法</span>
          <h1 className="mt-7 max-w-3xl text-4xl font-black leading-[1.1] tracking-tight sm:text-6xl lg:text-7xl">让科研工具<br /><span className="text-mint">真正为你所用</span></h1>
          <p className="mt-6 max-w-2xl text-base leading-8 text-white/60 sm:text-lg">汇集可复现的生物科研工作流、真实案例与专业教程。从第一次运行，到产出可靠结果。</p>
          <form onSubmit={search} className="relative mt-8 w-[calc(100vw-2rem)] max-w-2xl rounded-2xl bg-white p-2 shadow-2xl shadow-black/30 sm:w-full"><Search className="absolute left-5 top-1/2 -translate-y-1/2 text-slate-400" size={21} /><input value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="搜索 Seurat、AlphaFold、GSEA…" className="w-full rounded-xl py-3 pl-11 pr-24 text-sm text-slate-900 outline-none sm:pr-40 sm:text-base" /><button className="btn-mint absolute bottom-2 right-2 top-2 px-4 sm:px-6"><span className="sm:hidden">搜索</span><span className="hidden sm:inline">开始探索</span><ArrowRight size={16} className="hidden sm:block" /></button></form>
          <div className="mt-6 flex flex-wrap gap-4 text-xs text-white/45"><span>热门搜索</span>{['单细胞注释', '蛋白结构', '富集分析'].map(x => <button key={x} onClick={() => navigate(`/toolkits?q=${x}`)} className="border-b border-white/20 pb-0.5 hover:text-mint">{x}</button>)}</div>
        </div>
        <div className="relative hidden min-h-[470px] lg:block">
          <div className="absolute right-5 top-0 w-[86%] rotate-2 overflow-hidden rounded-[2rem] border border-white/10 bg-white/5 p-3 shadow-2xl backdrop-blur-xl animate-float"><img src="https://images.unsplash.com/photo-1532094349884-543bc11b234d?auto=format&fit=crop&w=1200&q=85" alt="科研人员" className="h-[390px] w-full rounded-[1.4rem] object-cover opacity-85" /><div className="absolute inset-x-7 bottom-7 rounded-2xl bg-ink/85 p-5 backdrop-blur-xl"><p className="text-xs text-mint">今日精选</p><p className="mt-1 font-bold">单细胞 RNA-seq 标准分析工作流</p><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/10"><div className="h-full w-3/4 rounded-full bg-mint" /></div></div></div>
          <div className="absolute -left-3 top-24 rounded-2xl border border-white/10 bg-white/10 p-4 shadow-xl backdrop-blur-xl"><p className="text-3xl font-black text-mint">{stats.toolkits.toLocaleString()}</p><p className="text-xs text-white/50">在架科研工作流</p></div>
          <div className="absolute bottom-8 left-10 rounded-2xl bg-mint p-4 text-ink shadow-glow"><UsersRound size={20} /><p className="mt-2 text-xl font-black">{stats.users.toLocaleString()}</p><p className="text-xs opacity-60">注册研究者</p></div>
        </div>
      </div>
    </section>

    <section className="mx-auto max-w-[1440px] px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
      <div className="flex items-end justify-between"><div><p className="text-sm font-bold uppercase tracking-[.2em] text-emerald-700">Explore</p><h2 className="mt-2 text-3xl font-black tracking-tight text-ink sm:text-4xl">从你的研究方向开始</h2></div><Link to="/toolkits" className="hidden items-center gap-2 text-sm font-bold text-pine sm:flex">全部分类 <ArrowRight size={17} /></Link></div>
      <div className="mt-9 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{categories.map(({ name, icon: Icon, color, detail }) => <Link key={name} to={`/toolkits?category=${name}`} className="group panel p-5 transition hover:-translate-y-1"><span className={`grid size-12 place-items-center rounded-2xl ${color}`}><Icon size={22} /></span><h3 className="mt-5 text-lg font-black text-ink">{name}</h3><p className="mt-1 text-sm text-slate-400">{detail}</p><ArrowRight className="mt-5 text-slate-300 transition group-hover:translate-x-1 group-hover:text-pine" size={18} /></Link>)}</div>
    </section>

    <section className="bg-white py-16 lg:py-24"><div className="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8"><div className="flex items-end justify-between"><div><p className="text-sm font-bold uppercase tracking-[.2em] text-emerald-700">Curated</p><h2 className="mt-2 text-3xl font-black tracking-tight text-ink sm:text-4xl">值得学习的精选工具包</h2><p className="mt-3 text-slate-500">经过实践验证的流程，帮你少走弯路。</p></div><Link to="/toolkits" className="hidden btn-ghost sm:flex">查看全部 <ArrowRight size={16} /></Link></div><div className="mt-9 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">{items.map(item => <ToolkitCard key={item.id} item={item} />)}</div></div></section>

    <section className="mx-auto max-w-[1440px] px-4 py-16 sm:px-6 lg:px-8 lg:py-24"><div className="relative overflow-hidden rounded-[2rem] bg-pine px-6 py-12 text-white sm:px-12 lg:flex lg:items-center lg:justify-between lg:px-16"><div className="absolute right-0 top-0 size-64 rounded-full bg-mint/10 blur-2xl" /><div className="relative"><span className="chip bg-white/10 text-mint"><BookOpenCheck size={14} />分享你的方法</span><h2 className="mt-5 text-3xl font-black sm:text-4xl">让你的科研经验，帮助更多人</h2><p className="mt-3 max-w-2xl text-white/55">发布完整的工具资源和图文教程。每次付费购买，你将获得 20% 的销售分成。</p></div><Link to="/publish" className="btn-mint relative mt-7 px-6 py-3.5 lg:mt-0">发布工具包 <ArrowRight size={17} /></Link></div></section>
    <footer className="border-t bg-white py-10"><div className="mx-auto flex max-w-[1440px] flex-col gap-4 px-4 text-sm text-slate-400 sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8"><span>© 2026 知核 · 让每一次探索都有章可循</span><span>科研工具仅供学习，请遵循数据与科研伦理规范</span></div></footer>
  </>
}
