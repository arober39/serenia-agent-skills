"use client";

import type { ActivityEntry } from "@/app/page";

interface DashboardProps {
  activity: ActivityEntry[];
}

function ScoreBadge({ score }: { score: string }) {
  const styles: Record<string, string> = {
    hot: "bg-red-900/50 text-red-400 border-red-800",
    warm: "bg-amber-900/50 text-amber-400 border-amber-800",
    cold: "bg-blue-900/50 text-blue-400 border-blue-800",
  };
  return (
    <span
      className={`text-xs px-2 py-0.5 rounded-full border ${styles[score] || "bg-gray-800 text-gray-400 border-gray-700"}`}
    >
      {score.toUpperCase()}
    </span>
  );
}

export default function Dashboard({ activity }: DashboardProps) {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 space-y-6">
      {/* Activity Feed */}
      <section>
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
          Activity Feed
        </h3>
        <div className="space-y-3">
          {activity.length === 0 && (
            <div className="text-sm text-gray-600">
              Send a message to see agent activity here.
            </div>
          )}
          {[...activity].reverse().map((entry, i) => (
            <div
              key={`${entry.message_id}-${i}`}
              className="bg-gray-800/50 border border-gray-800 rounded-lg px-4 py-3 space-y-2"
            >
              {/* Input preview */}
              <div className="text-xs text-gray-500 truncate">
                &quot;{entry.input}&quot;
              </div>

              {/* Routing flow */}
              <div className="flex items-center gap-2 text-xs">
                <span className="text-gray-400">Intent:</span>
                <span className="font-mono text-indigo-400">
                  {entry.detected_intent}
                </span>
                <span className="text-gray-600">&rarr;</span>
                <span className="text-gray-400">Routed:</span>
                <span className="font-mono text-emerald-400">
                  {entry.routed_to}
                </span>
                {entry.fallback && (
                  <span className="text-xs text-amber-500 bg-amber-900/30 border border-amber-800 px-1.5 py-0.5 rounded">
                    fallback
                  </span>
                )}
              </div>

              {/* Flag evaluation */}
              {entry.flag_evaluated && (
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-gray-400">Flag:</span>
                  <span className="font-mono text-gray-300">
                    {entry.flag_evaluated}
                  </span>
                  <span className="text-gray-600">=</span>
                  <span
                    className={
                      entry.flag_result ? "text-emerald-400" : "text-red-400"
                    }
                  >
                    {entry.flag_result ? "ON" : "OFF"}
                  </span>
                </div>
              )}

              {/* Lead scoring */}
              {entry.lead_score && (
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-gray-400">Lead:</span>
                  <ScoreBadge score={entry.lead_score} />
                  <span className="text-gray-600">&rarr;</span>
                  <span className="font-mono text-gray-300">
                    {entry.lead_action}
                  </span>
                </div>
              )}

              {/* Extracted info */}
              {(entry.extracted_name || entry.extracted_email) && (
                <div className="flex items-center gap-3 text-xs text-gray-500">
                  {entry.extracted_name && (
                    <span>Name: {entry.extracted_name}</span>
                  )}
                  {entry.extracted_email && (
                    <span>Email: {entry.extracted_email}</span>
                  )}
                </div>
              )}

              {/* Latency */}
              <div className="text-xs text-gray-600">
                {entry.latency_ms}ms
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
