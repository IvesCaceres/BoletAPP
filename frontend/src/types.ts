export type Client = {
  id: number;
  rut: string;
  razon_social: string;
  nombre_fantasia?: string | null;
  giro?: string | null;
  direccion: string;
  region: number;
  comuna: string;
  email?: string | null;
  detalle_default?: Array<Record<string, unknown>> | null;
  activo: boolean;
  created_at: string;
  updated_at: string;
};
export type ClientPayload = Omit<
  Partial<Client>,
  "id" | "created_at" | "updated_at" | "activo"
> & {
  rut: string;
  razon_social: string;
  direccion: string;
  region: number;
  comuna: string;
};
export type BatchItem = {
  id: number;
  batch_id: number;
  client_id: number | null;
  rut_receptor: string;
  razon_social: string;
  sii_resolved_recipient_name?: string | null;
  direccion: string;
  region: number;
  comuna: string;
  giro?: string | null;
  email?: string | null;
  fecha_emision?: string | null;
  detalle: Array<Record<string, unknown>> | string;
  monto: number;
  retention_mode_override?: RetentionMode | null;
  status: string;
  bhe_emission_id?: number | null;
  folio?: number | null;
  error_message?: string | null;
  final_click_occurred?: boolean;
  retryable?: boolean;
  retry_block_reason?: string | null;
};
export type Batch = {
  id: number;
  period: string;
  status: string;
  fecha_emision: string;
  detalle_global?: string | null;
  retention_mode?: RetentionMode | null;
  provider: string;
  source?: string;
  source_batch_id?: number | null;
  items: BatchItem[];
};
export type ManualBatchItemPayload = ManualBhePayload;
export type Preview = {
  batch_id: number;
  period: string;
  fecha_emision: string;
  cantidad: number;
  total: number;
  items: Array<{
    item_id: number;
    client_id: number;
    rut_receptor: string;
    razon_social: string;
    detalle: Array<Record<string, unknown>> | string;
    monto: number;
    retention_mode?: RetentionMode | null;
  }>;
};
export type EmissionResult = {
  item_id: number;
  status: string;
  bhe_emission_id?: number | null;
  folio?: number | null;
  error_message?: string | null;
};
export type BatchResult = {
  batch_id: number;
  status: string;
  provider: string;
  total: number;
  success: number;
  failed: number;
  unknown: number;
  pending: number;
  processing: number;
  results: EmissionResult[];
};
export type HistorySummary = {
  id: number;
  client_id?: number | null;
  nombre_fantasia?: string | null;
  rut_receptor: string;
  razon_social: string;
  direccion?: string;
  region?: number;
  comuna?: string;
  detalle?: Array<Record<string, unknown>> | string;
  monto_total: number;
  status: string;
  provider?: string;
  provider_message?: string | null;
  sii_resolved_recipient_name?: string | null;
  folio?: number | null;
  fecha_emision_solicitada: string;
  fecha_emision_provider?: string | null;
  created_at: string;
};
export type HistoryDetail = HistorySummary & {
  direccion: string;
  region: number;
  comuna: string;
  detalle: Array<Record<string, unknown>> | string;
  codigo_barras?: string | null;
  pdf_base64?: string | null;
  error_message?: string | null;
};
export type RetentionMode = "receiver_retains_ppm" | "issuer_handles_ppm";
export type ManualBhePayload = {
  rut_receptor: string;
  razon_social?: string;
  giro?: string;
  direccion: string;
  region: number;
  comuna: string;
  email?: string;
  detalle: [{ descripcion: string; cantidad: 1; precio_unitario: number }];
  fecha_emision: string;
  retention_mode?: RetentionMode;
};
export type BheResponse = {
  success: boolean;
  status?: number | string | null;
  message?: string | null;
  errors?: unknown;
  provider?: string;
  emission_id?: number | null;
  preview_validated?: boolean;
  recipient_rut_ok?: boolean | null;
  sii_resolved_recipient_name_present?: boolean | null;
  sii_resolved_recipient_name?: string | null;
  folio?: number | null;
  codigo_barras?: string | null;
  fecha_emision?: string | null;
  pdf_base64?: string | null;
};
