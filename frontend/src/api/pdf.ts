export function base64ToPdfBlobUrl(value: string): string {
  const bytes = Uint8Array.from(atob(value), char => char.charCodeAt(0))
  return URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }))
}
export function openPdf(value: string): void { window.open(base64ToPdfBlobUrl(value), '_blank', 'noopener,noreferrer') }
