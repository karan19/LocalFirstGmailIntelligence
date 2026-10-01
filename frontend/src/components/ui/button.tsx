import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
const variants = cva('inline-flex items-center justify-center gap-2 rounded-xl px-5 py-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-emerald-700 disabled:opacity-50 disabled:pointer-events-none', {variants:{variant:{default:'bg-emerald-900 text-white hover:bg-emerald-800',outline:'border border-stone-300 bg-white hover:bg-stone-50'}},defaultVariants:{variant:'default'}})
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof variants> {asChild?: boolean}
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({className,variant,asChild=false,...props},ref)=>{const Comp=asChild?Slot:'button';return <Comp className={twMerge(clsx(variants({variant}),className))} ref={ref} {...props}/>})
Button.displayName='Button'
