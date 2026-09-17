export type User = { id: number; username: string; nickname: string; role: 'admin' | 'user'; avatar?: string; bio: string; balance: number; frozenBalance: number }
export type Lesson = { id: number; title: string; content?: string; image?: string; sortOrder: number; duration: number; isPreview: boolean; locked: boolean }
export type Toolkit = { id: number; title: string; slug: string; summary: string; description?: string; category: string; level: string; cover?: string; price: number; author: { id: number; nickname: string; avatar?: string }; downloads: number; rating: number; featured: boolean; tags: string[]; status: string; purchased: boolean; favorited: boolean; lessonCount: number; lessons?: Lesson[]; resourceName?: string; createdAt: string }
export type Order = { id: number; orderNo: string; amount: number; commission: number; status: 'pending' | 'paid' | 'cancelled' | 'refunded'; toolkit: { id: number; title: string; cover?: string }; seller: string; createdAt: string; paidAt?: string }
export type Transaction = { id: number; type: 'recharge' | 'purchase' | 'commission' | 'withdrawal'; amount: number; balanceAfter: number; note: string; createdAt: string }

