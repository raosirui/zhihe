import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PublishPage } from '../pages/PublishPage'
import { setToken } from '../lib'
import { json, mockFetch, renderPage } from './helpers'

function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText(/工具包名称/), { target: { value: '可复现的分析流程' } })
  fireEvent.change(screen.getByLabelText(/工具包介绍/), { target: { value: '说明适用场景、运行环境和分析方法。' } })
}

describe('single-toolkit publishing form', () => {
  it('explains fields and constraints with accessible labels', async () => {
    setToken('publisher-session')
    mockFetch(() => undefined)
    renderPage(<PublishPage />)

    expect(await screen.findByLabelText(/工具包名称/)).toBeRequired()
    expect(screen.queryByLabelText(/一句话简介/)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/工具包介绍/)).toBeRequired()
    expect(screen.getByLabelText('标签（选填）')).toHaveAttribute('maxlength', '300')
    expect(screen.getByLabelText('封面图片 URL（选填）')).toHaveAttribute('maxlength', '500')

    const duration = screen.getByLabelText(/预计学习时长/)
    expect(duration).toHaveAttribute('min', '1')
    expect(duration).toHaveAttribute('max', '600')
    expect(duration).toHaveAttribute('step', '1')
    expect(duration).toBeRequired()
    expect(screen.getByText(/给学习者参考的用时，1–600 分钟整数/)).toBeInTheDocument()
    expect(screen.getByText(/每一步对应学习目录中的一节/)).toBeInTheDocument()
    expect(screen.getByText(/第 1 步默认开放试看/)).toBeInTheDocument()
    expect(screen.getByLabelText(/步骤正文/)).toBeRequired()

    fireEvent.click(screen.getByRole('button', { name: '添加步骤' }))
    expect(screen.getByRole('button', { name: '删除步骤 2' })).toBeInTheDocument()
  })

  it('lets an uploaded tutorial document replace typed body text and submits it as the first free preview', async () => {
    setToken('publisher-session')
    const { calls } = mockFetch(({ path, options }) => path === '/toolkits' && options.method === 'POST' ? json({ id: 44 }) : undefined)
    renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)
    fillRequiredFields()

    const body = screen.getByLabelText(/步骤正文/)
    expect(body).toBeRequired()
    const document = new File(['document contents'], 'guide.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    fireEvent.change(screen.getByLabelText('上传教程文件（选填）'), { target: { files: [document] } })
    expect(body).not.toBeRequired()
    expect(screen.getByText('已选择：guide.docx')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '发布工具包' }))
    await waitFor(() => expect(calls.some(call => call.path === '/toolkits' && call.options.method === 'POST')).toBe(true))
    const post = calls.find(call => call.path === '/toolkits' && call.options.method === 'POST')!
    const data = post.options.body as FormData
    expect(data.get('summary')).toBeNull()
    expect(data.get('description')).toBe('说明适用场景、运行环境和分析方法。')
    const submittedLessons = JSON.parse(String(data.get('lessons')))
    expect(submittedLessons[0].preview).toBe(true)
    expect(submittedLessons[0].content).toBe('')
    expect((data.get('lesson_file_0') as File).name).toBe('guide.docx')
  })

  it('accepts Markdown/TXT through the tutorial document picker without a separate import control', async () => {
    setToken('publisher-session')
    mockFetch(() => undefined)
    renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)

    const documentPicker = screen.getByLabelText('上传教程文件（选填）')
    expect(documentPicker).toHaveAttribute('accept', expect.stringContaining('.md'))
    expect(documentPicker).toHaveAttribute('accept', expect.stringContaining('.markdown'))
    expect(documentPicker).toHaveAttribute('accept', expect.stringContaining('.txt'))
    expect(screen.queryByText('导入 Markdown/TXT')).not.toBeInTheDocument()
  })

  it('uploads cover and lesson images by zero-based index, with file taking precedence over preserved URLs', async () => {
    setToken('publisher-session')
    const create = vi.fn(() => 'blob:image'), revoke = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }))
    const { calls } = mockFetch(({ path, options }) => path === '/toolkits' && options.method === 'POST' ? json({ id: 77 }) : undefined)
    const result = renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)
    fillRequiredFields()
    fireEvent.change(screen.getByLabelText(/步骤正文/), { target: { value: '可复现步骤' } })
    fireEvent.change(screen.getByLabelText('封面图片 URL（选填）'), { target: { value: 'https://example.org/old-cover.jpg' } })
    fireEvent.change(screen.getByLabelText('本步骤配图 URL（选填）'), { target: { value: 'https://example.org/old-step.jpg' } })
    const cover = new File(['cover'], 'cover.png', { type: 'image/png' })
    const image = new File(['step'], 'step.jpg', { type: 'image/jpeg' })
    fireEvent.change(screen.getByLabelText('或上传封面图片（选填）'), { target: { files: [cover] } })
    fireEvent.change(screen.getByLabelText('或上传本步骤配图（选填）'), { target: { files: [image] } })
    expect(screen.getByLabelText('封面图片 URL（选填）')).toBeDisabled()
    expect(screen.getByLabelText('本步骤配图 URL（选填）')).toBeDisabled()
    expect(await screen.findByRole('img', { name: '封面缩略图' })).toHaveAttribute('src', 'blob:image')
    expect(screen.getByRole('img', { name: '第 1 步配图缩略图' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '移除封面文件' }))
    expect(screen.getByLabelText('封面图片 URL（选填）')).toHaveValue('https://example.org/old-cover.jpg')
    expect(screen.getByLabelText('封面图片 URL（选填）')).toBeEnabled()
    fireEvent.change(screen.getByLabelText('或上传封面图片（选填）'), { target: { files: [cover] } })
    fireEvent.click(screen.getByRole('button', { name: '发布工具包' }))
    await waitFor(() => expect(calls.some(call => call.path === '/toolkits' && call.options.method === 'POST')).toBe(true))
    const data = calls.find(call => call.path === '/toolkits' && call.options.method === 'POST')!.options.body as FormData
    expect(data.get('cover')).toBe('')
    expect((data.get('cover_file') as File).name).toBe('cover.png')
    expect((data.get('lesson_image_0') as File).name).toBe('step.jpg')
    expect(JSON.parse(String(data.get('lessons')))[0].image).toBe('')
    result.unmount()
    expect(revoke).toHaveBeenCalledWith('blob:image')
  })

  it('counts images with documents and resources toward upload caps without losing accepted files', async () => {
    setToken('publisher-session')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:image'), revokeObjectURL: vi.fn() }))
    mockFetch(() => undefined)
    renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)
    const sized = (name: string, mb: number, type = '') => {
      const file = new File(['small'], name, { type })
      Object.defineProperty(file, 'size', { value: mb * 1024 * 1024 })
      return file
    }
    fireEvent.change(screen.getByLabelText('或上传封面图片（选填）'), { target: { files: [sized('cover.png', 39, 'image/png')] } })
    fireEvent.change(screen.getByLabelText('或上传本步骤配图（选填）'), { target: { files: [sized('step.jpg', 39, 'image/jpeg')] } })
    fireEvent.change(screen.getByLabelText(/点击选择资源文件/), { target: { files: [sized('data.zip', 23)] } })
    expect(screen.getByRole('alert')).toHaveTextContent('不能超过 100 MB')
    expect(screen.getByText('点击选择资源文件（可多选）')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '封面缩略图' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('或上传本步骤配图（选填）'), { target: { files: [sized('too-large.png', 41, 'image/png')] } })
    expect(screen.getAllByRole('alert').some(alert => alert.textContent?.includes('too-large.png 超过单个文件 40 MB'))).toBe(true)
    expect(screen.getByText('已选择：step.jpg')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('或上传封面图片（选填）'), { target: { files: [new File(['svg'], 'image.svg', { type: 'image/svg+xml' })] } })
    expect(screen.getAllByRole('alert').some(alert => alert.textContent?.includes('不支持 SVG'))).toBe(true)
  })

  it('previews selected HTML/text files only as inert text and clears image URLs on logout', async () => {
    setToken('publisher-session')
    const revoke = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:local-cover'), revokeObjectURL: revoke }))
    mockFetch(({ path }) => path === '/auth/logout' ? json({ ok: true }) : undefined)
    const result = renderPage(<PublishPage />, '/', '/', undefined, true)
    await screen.findByLabelText(/工具包名称/)
    fireEvent.change(screen.getByLabelText('或上传封面图片（选填）'), { target: { files: [new File(['image'], 'cover.png', { type: 'image/png' })] } })
    fireEvent.change(screen.getByLabelText(/点击选择资源文件/), { target: {
      files: [new File(['<script>alert(1)</script><h1>plain</h1>'], 'sample.html', { type: 'text/html' })],
    } })
    fireEvent.click(screen.getByText('预览资源：sample.html'))
    expect(await screen.findByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument()
    expect(result.container.querySelector('script')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Logout session' }))
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:local-cover'))
    expect(screen.queryByText('sample.html')).not.toBeInTheDocument()
  })

  it('reindexes an uploaded lesson image when an earlier lesson is removed', async () => {
    setToken('publisher-session')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:image'), revokeObjectURL: vi.fn() }))
    const { calls } = mockFetch(({ path, options }) => path === '/toolkits' && options.method === 'POST' ? json({ id: 78 }) : undefined)
    renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)
    fillRequiredFields()
    fireEvent.click(screen.getByRole('button', { name: '添加步骤' }))
    fireEvent.change(screen.getAllByLabelText(/步骤标题/)[1], { target: { value: '后一个步骤' } })
    fireEvent.change(screen.getAllByLabelText(/步骤正文/)[1], { target: { value: '正文' } })
    fireEvent.change(screen.getAllByLabelText('或上传本步骤配图（选填）')[1], { target: { files: [new File(['x'], 'step.png', { type: 'image/png' })] } })
    fireEvent.click(screen.getByRole('button', { name: '删除步骤 1' }))
    fireEvent.click(screen.getByRole('button', { name: '发布工具包' }))
    await waitFor(() => expect(calls.some(call => call.path === '/toolkits' && call.options.method === 'POST')).toBe(true))
    const data = calls.find(call => call.path === '/toolkits' && call.options.method === 'POST')!.options.body as FormData
    expect((data.get('lesson_image_0') as File).name).toBe('step.png')
    expect(data.get('lesson_image_1')).toBeNull()
    expect(JSON.parse(String(data.get('lessons')))[0]).toMatchObject({ title: '后一个步骤', preview: true })
  })

  it('rejects excess resources without truncating or replacing already-selected files', async () => {
    setToken('publisher-session')
    mockFetch(() => undefined)
    renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)
    const resourcePicker = screen.getByLabelText(/点击选择资源文件/)
    const first = new File(['small'], 'first.zip')
    fireEvent.change(resourcePicker, { target: { files: [first] } })
    expect(screen.getByText('已选择 1 个文件')).toBeInTheDocument()

    const extra = Array.from({ length: 20 }, (_, index) => new File(['small'], `extra-${index}.zip`))
    fireEvent.change(resourcePicker, { target: { files: extra } })
    expect(screen.getByRole('alert')).toHaveTextContent('最多选择 20 个')
    expect(screen.getByRole('alert')).toHaveTextContent('原有已选文件已保留')
    expect(screen.getByText('已选择 1 个文件')).toBeInTheDocument()
  })

  it('reports oversized resource selections immediately', async () => {
    setToken('publisher-session')
    mockFetch(() => undefined)
    renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)
    const oversized = new File(['x'], 'large.zip')
    Object.defineProperty(oversized, 'size', { value: 40 * 1024 * 1024 + 1 })
    fireEvent.change(screen.getByLabelText(/点击选择资源文件/), { target: { files: [oversized] } })
    expect(screen.getByRole('alert')).toHaveTextContent('large.zip 超过单个文件 40 MB')
    expect(screen.getByText('点击选择资源文件（可多选）')).toBeInTheDocument()
  })

  it('saves valid drafts with draft status and keeps the preview-step contract', async () => {
    setToken('publisher-session')
    const { calls } = mockFetch(({ path, options }) => path === '/toolkits' && options.method === 'POST' ? json({ id: 45 }) : undefined)
    renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)
    fillRequiredFields()

    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
    await waitFor(() => expect(calls.some(call => call.path === '/toolkits' && call.options.method === 'POST')).toBe(true))
    const data = calls.find(call => call.path === '/toolkits' && call.options.method === 'POST')!.options.body as FormData
    expect(data.get('status')).toBe('draft')
    expect(JSON.parse(String(data.get('lessons')))[0].preview).toBe(true)
  })

  it('saves a draft with incomplete required fields and blank numeric values', async () => {
    setToken('publisher-session')
    const { calls } = mockFetch(({ path, options }) => path === '/toolkits' && options.method === 'POST' ? json({ id: 46 }) : undefined)
    renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)

    fireEvent.change(screen.getByLabelText(/定价（元）/), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText(/步骤标题/), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText(/预计学习时长/), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(calls.some(call => call.path === '/toolkits' && call.options.method === 'POST')).toBe(true))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    const data = calls.find(call => call.path === '/toolkits' && call.options.method === 'POST')!.options.body as FormData
    expect(data.get('status')).toBe('draft')
    expect(data.get('title')).toBe('')
    expect(data.get('description')).toBe('')
    expect(data.get('price')).toBe('')
    expect(JSON.parse(String(data.get('lessons')))[0]).toMatchObject({ title: '', content: '', duration: 0 })
  })

  it('validates incomplete content when publishing instead of saving a draft', async () => {
    setToken('publisher-session')
    const { calls } = mockFetch(() => undefined)
    const result = renderPage(<PublishPage />)
    await screen.findByLabelText(/工具包名称/)

    fireEvent.submit(result.container.querySelector('form')!)

    expect(screen.getByRole('alert')).toHaveTextContent('请填写工具包名称')
    expect(calls.some(call => call.path === '/toolkits' && call.options.method === 'POST')).toBe(false)
  })
})
