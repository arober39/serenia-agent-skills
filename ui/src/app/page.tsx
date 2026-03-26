"use client";

import { useState } from "react";
import Chat from "@/components/Chat";
import Dashboard from "@/components/Dashboard";

export interface ActivityEntry {
  message_id: string;
  input: string;
  context_key: string;
  detected_intent: string | null;
  routed_to: string | null;
  flag_evaluated: string | null;
  flag_result: boolean | null;
  fallback: boolean;
  lead_score: string | null;
  lead_action: string | null;
  latency_ms: number | null;
  extracted_name: string | null;
  extracted_email: string | null;
}

const API_BASE = "http://localhost:8000";

export default function Home() {
  const [activity, setActivity] = useState<ActivityEntry[]>([]);

  const addActivity = (entry: ActivityEntry) => {
    setActivity((prev) => [...prev, entry]);
  };

  return (
    <div className="flex h-screen">
      {/* Left: Chat */}
      <div className="w-1/2 border-r border-gray-800 flex flex-col">
        <div className="px-6 py-4 border-b border-gray-800">
          <h1 className="text-xl font-semibold">Serenia</h1>
          <p className="text-sm text-gray-400">
            Event Venue AI Assistant
          </p>
        </div>
        <Chat apiBase={API_BASE} onActivity={addActivity} />
      </div>

      {/* Right: Dashboard */}
      <div className="w-1/2 flex flex-col bg-gray-900">
        <div className="px-6 py-4 border-b border-gray-800">
          <h2 className="text-xl font-semibold">Agent Dashboard</h2>
          <p className="text-sm text-gray-400">
            Skill routing, flags & activity
          </p>
        </div>
        <Dashboard activity={activity} />
      </div>
    </div>
  );
}
