import type { Role } from "../../types";
import { getRolePdfUrl } from "../../api/reportsApi";

interface Props {
  batchId: string;
  role: Role;
}

const S = {
  btn: {
    background: "transparent",
    border: "1px solid #333",
    color: "#737373",
    padding: "5px 12px",
    fontSize: "11px",
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    cursor: "pointer",
    textDecoration: "none",
  },
};

export default function PDFDownloadButton({ batchId, role }: Props) {
  return (
    <a
      href={getRolePdfUrl(batchId, role)}
      target="_blank"
      rel="noopener noreferrer"
      style={S.btn}
    >
      PDF
    </a>
  );
}
