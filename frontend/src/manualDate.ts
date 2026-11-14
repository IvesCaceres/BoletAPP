/** AppBoletas runs locally, so manual issue dates use the device's calendar day. */
const MAX_ISSUE_AGE_DAYS = 90;

const localIsoDate = (value: Date) =>
  `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;

export const manualDateBounds = (now = new Date()) => {
  const maximum = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const minimum = new Date(now.getFullYear(), now.getMonth(), now.getDate() - MAX_ISSUE_AGE_DAYS);
  return { min: localIsoDate(minimum), max: localIsoDate(maximum) };
};

export const manualDateError = (value: string, now = new Date()): string | null => {
  const { min, max } = manualDateBounds(now);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "Selecciona una fecha de emisión válida.";
  const [year, month, day] = value.split("-").map(Number);
  if (localIsoDate(new Date(year, month - 1, day)) !== value) return "Selecciona una fecha de emisión válida.";
  if (value < min) return "La fecha de emisión no puede tener más de 90 días de antigüedad.";
  if (value > max) return "La fecha de emisión no puede ser futura.";
  return null;
};
