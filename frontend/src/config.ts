/**
 * Runtime API base URL.
 *
 * Vite exposes env vars prefixed with VITE_ via import.meta.env.
 * - In development, leave this unset; the Vite dev proxy forwards /api
 *   requests to the local backend (http://localhost:8000).
 * - In production (Vercel), set this to the full Render backend origin, e.g.
 *   https://your-backend.onrender.com (no trailing slash).
 */
export const API_BASE_URL = import.meta.env.VITE_API_URL || "";
