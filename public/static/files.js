const sizeFormat = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });

export function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 ** 2) return sizeFormat.format(bytes / 1024) + " KiB";
  return sizeFormat.format(bytes / 1024 ** 2) + " MiB";
}

export function saveDownload(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
