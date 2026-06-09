import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors",
  {
    variants: {
      variant: {
        default: "bg-slate-700 text-slate-200",
        high: "bg-emerald-950 text-emerald-400 border border-emerald-800",
        medium: "bg-amber-950 text-amber-400 border border-amber-800",
        low: "bg-rose-950 text-rose-400 border border-rose-800",
        outline: "border border-slate-700 text-slate-400",
        blue: "bg-blue-950 text-blue-400 border border-blue-800",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
