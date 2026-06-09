"use client";

import { Check, Loader2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TaskStatus } from "@/lib/types";

const STEPS = [
  { id: "plan",     label: "Generating Sub-questions",  description: "Decomposing your query into research vectors" },
  { id: "rag",      label: "Fetching Evidence",          description: "Retrieving relevant intelligence from corpus" },
  { id: "signals",  label: "Fetching Market Signals",    description: "Pulling live prices & macro indicators" },
  { id: "analysis", label: "Synthesizing Final Report",  description: "LLM generating structured risk analysis" },
];

function getActiveStep(status: TaskStatus): number {
  if (status === "PENDING") return -1;
  if (status === "PROCESSING") return 1; // approximate — backend doesn't stream sub-step state yet
  if (status === "SUCCESS") return STEPS.length;
  if (status === "FAILED") return -2;
  return -1;
}

interface AgentStepperProps {
  status: TaskStatus;
  error?: string | null;
}

export function AgentStepper({ status, error }: AgentStepperProps) {
  const activeStep = getActiveStep(status);
  const failed = status === "FAILED";

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-4">
        Agent Execution
      </p>

      <div className="relative">
        {/* Connector line */}
        <div className="absolute left-4 top-5 bottom-5 w-px bg-slate-800" />

        <div className="flex flex-col gap-5">
          {STEPS.map((step, idx) => {
            const done = activeStep > idx || status === "SUCCESS";
            const active = activeStep === idx || (status === "PROCESSING" && idx <= 2);
            const pending = !done && !active;

            return (
              <div key={step.id} className="relative flex items-start gap-4 pl-0">
                {/* Icon */}
                <div
                  className={cn(
                    "relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 transition-all",
                    done && !failed
                      ? "border-emerald-500 bg-emerald-950"
                      : active && !failed
                      ? "border-blue-500 bg-blue-950 pulse-active"
                      : failed && idx === activeStep
                      ? "border-rose-500 bg-rose-950"
                      : "border-slate-700 bg-slate-900"
                  )}
                >
                  {done && !failed ? (
                    <Check className="h-4 w-4 text-emerald-400" />
                  ) : active && !failed ? (
                    <Loader2 className="h-4 w-4 text-blue-400 animate-spin" />
                  ) : failed && idx >= activeStep ? (
                    <AlertCircle className="h-4 w-4 text-rose-400" />
                  ) : (
                    <span className="text-xs font-semibold text-slate-600">{idx + 1}</span>
                  )}
                </div>

                {/* Label */}
                <div className="pt-0.5">
                  <p
                    className={cn(
                      "text-sm font-medium",
                      done && !failed
                        ? "text-emerald-400"
                        : active && !failed
                        ? "text-blue-300"
                        : "text-slate-500"
                    )}
                  >
                    {step.label}
                  </p>
                  <p className="text-xs text-slate-600 mt-0.5">{step.description}</p>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {failed && error && (
        <div className="mt-4 rounded-lg border border-rose-800 bg-rose-950/40 p-3">
          <p className="text-xs text-rose-400">
            <span className="font-semibold">Error:</span> {error}
          </p>
        </div>
      )}
    </div>
  );
}
