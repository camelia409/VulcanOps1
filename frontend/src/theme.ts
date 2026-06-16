// Light industrial dashboard theme used across the VulcanOps UI.
export const COLORS = {
  // surfaces
  pageBg: "#f5f4f0",
  cardBg: "#ffffff",
  headerBg: "#f5f4f0",
  inputBg: "#fafaf9",
  hoverBg: "#f5f5f4",

  // borders
  border: "#e7e5e4",
  borderStrong: "#d6d3d1",

  // text
  text: "#1c1917",
  textSecondary: "#57534e",
  textMuted: "#78716c",
  textLight: "#a8a29e",

  // accents
  accent: "#4f46e5",
  accentLight: "#eef2ff",
  accentText: "#4338ca",

  // status
  done: "#16a34a",
  doneBg: "#dcfce7",
  running: "#2563eb",
  runningBg: "#dbeafe",
  pending: "#d97706",
  pendingBg: "#fef3c7",
  failed: "#dc2626",
  failedBg: "#fee2e2",

  // priority
  urgent: "#ea580c",
  urgentBg: "#ffedd5",
  routine: "#16a34a",
  routineBg: "#dcfce7",
  emergency: "#dc2626",
  emergencyBg: "#fee2e2",
  scheduled: "#d97706",
  scheduledBg: "#fef3c7",
};

export const STATUS_COLORS: Record<string, { bg: string; text: string }> = {
  done: { bg: COLORS.doneBg, text: COLORS.done },
  success: { bg: COLORS.doneBg, text: COLORS.done },
  running: { bg: COLORS.runningBg, text: COLORS.running },
  pending: { bg: COLORS.pendingBg, text: COLORS.pending },
  processing: { bg: "#ede9fe", text: "#6d28d9" },
  queued: { bg: "#f0fdf4", text: "#15803d" },
  failed: { bg: COLORS.failedBg, text: COLORS.failed },
  error: { bg: COLORS.failedBg, text: COLORS.failed },
};

export const PRIORITY_COLORS: Record<string, { bg: string; text: string }> = {
  emergency: { bg: COLORS.emergencyBg, text: COLORS.emergency },
  urgent: { bg: COLORS.urgentBg, text: COLORS.urgent },
  scheduled: { bg: COLORS.scheduledBg, text: COLORS.scheduled },
  routine: { bg: COLORS.routineBg, text: COLORS.routine },
};
