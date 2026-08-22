export default function UploadButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="upload-btn" onClick={onClick}>
      + Upload
    </button>
  );
}
