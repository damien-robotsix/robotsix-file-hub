export default function UploadButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "0.4rem 0.9rem",
        fontSize: "0.9rem",
        cursor: "pointer",
        background: "#0d6efd",
        color: "#fff",
        border: "none",
        borderRadius: "4px",
      }}
    >
      + Upload
    </button>
  );
}
