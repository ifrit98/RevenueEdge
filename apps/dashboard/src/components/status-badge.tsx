const colorMap: Record<string, string> = {
  open: "bg-blue-100 text-blue-700",
  active: "bg-blue-100 text-blue-700",
  new: "bg-blue-100 text-blue-700",
  sent: "bg-blue-100 text-blue-700",
  contacted: "bg-indigo-100 text-indigo-700",
  qualified: "bg-purple-100 text-purple-700",
  proposal: "bg-violet-100 text-violet-700",
  awaiting_customer: "bg-yellow-100 text-yellow-700",
  awaiting_review: "bg-yellow-100 text-yellow-700",
  draft: "bg-yellow-100 text-yellow-700",
  tentative: "bg-yellow-100 text-yellow-700",
  awaiting_human: "bg-orange-100 text-orange-700",
  escalated: "bg-orange-100 text-orange-700",
  resolved: "bg-green-100 text-green-700",
  completed: "bg-green-100 text-green-700",
  done: "bg-green-100 text-green-700",
  won: "bg-green-100 text-green-700",
  confirmed: "bg-green-100 text-green-700",
  approved: "bg-green-100 text-green-700",
  booked: "bg-teal-100 text-teal-700",
  closed: "bg-gray-100 text-gray-600",
  cancelled: "bg-gray-100 text-gray-600",
  void: "bg-gray-100 text-gray-600",
  lost: "bg-red-100 text-red-700",
  no_show: "bg-red-100 text-red-700",
};

export function StatusBadge({ status, size = "sm" }: { status: string; size?: "sm" | "md" }) {
  const cls = colorMap[status] || "bg-gray-100 text-gray-600";
  const sz = size === "md" ? "px-2.5 py-1 text-xs" : "px-2 py-0.5 text-xs";
  return (
    <span className={`inline-flex items-center rounded-full font-medium ${cls} ${sz}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}
