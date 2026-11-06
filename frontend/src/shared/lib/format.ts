import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export const cn = (...values: ClassValue[]) => twMerge(clsx(values))
export const money = (value: number) => value === 0 ? '免费' : `¥${value % 1 === 0 ? String(value) : value.toFixed(2)}`
export const date = (value: string) => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
