import { FormEvent, Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { clientsApi } from "./api/clients";
import { batchesApi } from "./api/batches";
import { historyApi } from "./api/history";
import { confirmFinalBhe, emitBhe } from "./api/bhe";
import { openPdf } from "./api/pdf";
import { userFacingError } from "./api/client";
import { getCommunesByRegion, getRegions } from "./data/chileLocations";
import { manualDateBounds, manualDateError } from "./manualDate";
import type {
  Batch,
  BatchResult,
  BheResponse,
  Client,
  ClientPayload,
  HistoryDetail,
  HistorySummary,
  ManualBhePayload,
  RetentionMode,
  Preview,
} from "./types";

const money = (n: number) =>
  new Intl.NumberFormat("es-CL", {
    style: "currency",
    currency: "CLP",
    maximumFractionDigits: 0,
  }).format(n);
const detailText = (d: unknown) =>
  typeof d === "string"
    ? d
    : Array.isArray(d)
      ? String((d[0] as Record<string, unknown> | undefined)?.descripcion || "")
      : "";
const today = new Date().toISOString().slice(0, 10);
const periodNow = today.slice(0, 7);
const monthNames = [
  "ENERO",
  "FEBRERO",
  "MARZO",
  "ABRIL",
  "MAYO",
  "JUNIO",
  "JULIO",
  "AGOSTO",
  "SEPTIEMBRE",
  "OCTUBRE",
  "NOVIEMBRE",
  "DICIEMBRE",
];
export const defaultGlobalDetail = (period: string) => {
  const [year, month] = period.split("-");
  return `SERVICIOS ECOGRAFÍA MES ${monthNames[Number(month) - 1]} ${year}`;
};

function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="alert error">{userFacingError(error)}</div>;
}

function RetentionSelector({
  value,
  onChange,
  name,
  variant,
}: {
  value: RetentionMode;
  onChange: (value: RetentionMode) => void;
  name: string;
  variant: "long" | "short";
}) {
  const options = variant === "long"
    ? [
        ["receiver_retains_ppm", "El receptor retiene el PPM", "El receptor retiene el PPM"],
        ["issuer_handles_ppm", "El emisor se encarga del PPM", "El emisor se encarga del PPM"],
      ]
    : [
        ["receiver_retains_ppm", "Receptor", "El receptor retiene el PPM"],
        ["issuer_handles_ppm", "Emisor", "El emisor se encarga del PPM"],
      ];
  return (
    <fieldset className={`retention-options retention-options--${variant}`}>
      <legend>{variant === "long" ? "Retención de la boleta" : "Retención predeterminada del lote"}</legend>
      {options.map(([option, text, ariaLabel]) => (
        <label className="retention-option" key={option}>
          <input
            type="radio"
            name={name}
            value={option}
            aria-label={ariaLabel}
            checked={value === option}
            onChange={() => onChange(option as RetentionMode)}
          />
          <span>{text}</span>
        </label>
      ))}
    </fieldset>
  );
}

function LocationFields({
  region,
  comuna,
  onRegionChange,
  onComunaChange,
}: {
  region: string;
  comuna: string;
  onRegionChange: (value: string) => void;
  onComunaChange: (value: string) => void;
}) {
  const communes = getCommunesByRegion(region);
  return (
    <>
      <label>
        Región
        <select
          aria-label="Región"
          value={region}
          onChange={(event) => onRegionChange(event.target.value)}
          required
        >
          <option value="">Selecciona una región</option>
          {getRegions().map((item) => (
            <option key={item.code} value={item.code}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Comuna
        <select
          aria-label="Comuna"
          value={comuna}
          onChange={(event) => onComunaChange(event.target.value)}
          disabled={!region}
          required
        >
          <option value="">
            {region ? "Selecciona una comuna" : "Selecciona primero una región"}
          </option>
          {communes.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}
function Confirm({
  title,
  text,
  onCancel,
  onConfirm,
  busy,
  confirmLabel = "Confirmar emisión",
}: {
  title: string;
  text: string;
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
  confirmLabel?: string;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>{title}</h2>
        <p>{text}</p>
        <div className="actions">
          <button className="secondary" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button className="danger" onClick={onConfirm} disabled={busy}>
            {busy ? "Procesando…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
function Nav({
  view,
  go,
  theme,
  toggleTheme,
  open,
  onClose,
}: {
  view: string;
  go: (v: string) => void;
  theme: "light" | "dark";
  toggleTheme: () => void;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <aside className={open ? "sidebar open" : "sidebar"} id="app-navigation">
      <button className="drawer-close" type="button" aria-label="Cerrar menú" onClick={onClose}>×</button>
      <div className="brand">AppBoletas</div>
      <button
        className={view === "month" ? "nav active" : "nav"}
        onClick={() => go("month")}
      >
        Emisión mensual
      </button>
      <button
        className={view === "new" ? "nav active" : "nav"}
        onClick={() => go("new")}
      >
        Nueva boleta
      </button>
      <button
        className={view === "clients" ? "nav active" : "nav"}
        onClick={() => go("clients")}
      >
        Clientes
      </button>
      <button
        className={view === "history" ? "nav active" : "nav"}
        onClick={() => go("history")}
      >
        Historial
      </button>
      <button className="theme-toggle" onClick={toggleTheme}>
        {theme === "dark" ? "Modo claro" : "Modo oscuro"}
      </button>
    </aside>
  );
}

const displayName = (client: Client) =>
  client.nombre_fantasia?.trim() || client.razon_social;

const historyClient = (item: HistorySummary): Client => ({
  id: item.client_id ?? 0,
  rut: item.rut_receptor,
  razon_social: item.razon_social,
  nombre_fantasia: item.nombre_fantasia,
  direccion: item.direccion || "",
  region: item.region || 0,
  comuna: item.comuna || "",
  activo: true,
  created_at: item.created_at,
  updated_at: item.created_at,
});

function formatRut(value: string): string {
  const cleaned = value.replace(/[.\-\s]/g, "").toUpperCase();
  if (cleaned.length < 2) return value;
  const body = cleaned.slice(0, -1);
  const verifier = cleaned.slice(-1);
  return `${body.replace(/\B(?=(\d{3})+(?!\d))/g, ".")}-${verifier}`;
}

function ClientInfoPopover({ client, detail, retention }: {
  client: Client;
  detail?: string;
  retention?: string;
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLSpanElement>(null);
  const closeTimer = useRef<number | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const show = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    setOpen(true);
  };
  const hideSoon = () => {
    closeTimer.current = window.setTimeout(() => setOpen(false), 100);
  };
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const anchor = triggerRef.current.getBoundingClientRect();
    const popover = popoverRef.current;
    const width = popover?.offsetWidth || 320;
    const height = popover?.offsetHeight || 240;
    const gap = 8;
    const left = Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8));
    const below = anchor.bottom + gap;
    const top = below + height <= window.innerHeight - 8
      ? below
      : Math.max(8, anchor.top - height - gap);
    setPosition({ left, top });
  }, [open]);
  const popover = open ? createPortal(<span ref={popoverRef} className="client-info-popover" role="tooltip" style={{ left: position.left, top: position.top }} onMouseEnter={show} onMouseLeave={hideSoon}>
        <strong>{displayName(client)}</strong>
        <span>RUT: {formatRut(client.rut)}</span>
        {client.nombre_fantasia?.trim() && <span>Razón social: {client.razon_social}</span>}
        {client.direccion && <span>Dirección: {client.direccion}</span>}
        {client.comuna && <span>Comuna: {client.comuna}</span>}
        {client.region && <span>Región: {getRegions().find((r) => r.code === client.region)?.name || client.region}</span>}
        {client.email && <span>Email: {client.email}</span>}
        {detail && <span>Detalle: {detail}</span>}
        {client.giro && <span>Giro: {client.giro}</span>}
        {retention && <span>Retención: {retention}</span>}
      </span>, document.body) : null;
  return <span className="client-info-wrap" onMouseEnter={show} onMouseLeave={hideSoon}>
    <button ref={triggerRef} type="button" className="client-info" aria-label={`Información de ${displayName(client)}`} aria-expanded={open} onClick={(event) => { event.stopPropagation(); show(); }} onFocus={show} onBlur={(event) => { if (!event.relatedTarget || !popoverRef.current?.contains(event.relatedTarget as Node)) hideSoon(); }}>ⓘ</button>
    {popover}
  </span>;
}

const batchStatusLabels: Record<string, string> = {
  draft: "Borrador",
  ready: "Listo",
  pending: "Pendiente",
  processing: "Emitiendo",
  success: "Emitida",
  completed: "Finalizado",
  completed_with_errors: "Finalizado con errores",
  failed: "Error",
  emission_status_unknown: "Estado desconocido",
};

const batchStatusLabel = (status: string) => batchStatusLabels[status] || "Estado desconocido";

function MonthView() {
  const [period, setPeriod] = useState(periodNow),
    [fecha, setFecha] = useState(today),
    [detailGlobal, setDetailGlobal] = useState(defaultGlobalDetail(periodNow)),
    [retentionMode, setRetentionMode] = useState<RetentionMode>("issuer_handles_ppm"),
    [batch, setBatch] = useState<Batch | null>(null),
    [clients, setClients] = useState<Client[]>([]),
    [selected, setSelected] = useState<number[]>([]),
    [amounts, setAmounts] = useState<Record<number, number>>({}),
    [details, setDetails] = useState<Record<number, string>>({}),
    [preview, setPreview] = useState<Preview | null>(null),
    [result, setResult] = useState<BatchResult | null>(null),
    [confirm, setConfirm] = useState(false),
    [itemRetryId, setItemRetryId] = useState<number | null>(null),
    [newBatchConfirm, setNewBatchConfirm] = useState(false),
    [busy, setBusy] = useState(false),
    [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle"),
    [dirty, setDirty] = useState(false),
    [search, setSearch] = useState(""),
    [retentionOverrides, setRetentionOverrides] = useState<Record<number, RetentionMode | null>>({}),
    [editingItemId, setEditingItemId] = useState<number | null>(null),
    [editDraft, setEditDraft] = useState<{ monto: string; detalle: string; retention: RetentionMode | "" }>({ monto: "", detalle: "", retention: "" }),
    [error, setError] = useState<unknown>(null);
  const load = async () => {
    try {
      setError(null);
      const [all, bs] = await Promise.all([
        clientsApi.list(),
        batchesApi.list(),
      ]);
      setClients(all.filter((c) => c.activo));
      const eligible = bs.filter((b) => b.source !== "manual" && ["draft", "ready", "processing", "completed", "completed_with_errors"].includes(b.status));
      const found = eligible.find((b) => b.status === "processing")
        || eligible.find((b) => b.status === "draft")
        || eligible.find((b) => b.period === period)
        || eligible[0];
      if (found) {
        const loaded = await batchesApi.get(found.id);
        setBatch(loaded);
        setFecha(loaded.fecha_emision);
        setDetailGlobal(loaded.detalle_global || defaultGlobalDetail(period));
        setRetentionMode(loaded.retention_mode || "issuer_handles_ppm");
      } else setBatch(null);
      setDirty(false);
      setSaveState("idle");
    } catch (e) {
      setError(e);
    }
  };
  useEffect(() => {
    void load();
  }, [period]);
  useEffect(() => {
    if (!batch || batch.status !== "processing") return;
    const timer = window.setInterval(() => {
      void batchesApi.get(batch.id).then(setBatch).catch(setError);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [batch?.id, batch?.status]);
  const editable = batch?.status === "draft";
  const selectable = clients.filter((c) => !batch?.items.some((i) => i.client_id === c.id));
  const available = selectable.filter((c) => !selected.includes(c.id));
  const isEligible = (client: Client) => Boolean(detailGlobal.trim() || detailText(client.detalle_default));
  const matchesSearch = (c: Client) => {
    const q = search.trim().toLocaleLowerCase();
    return !q || [c.nombre_fantasia, c.razon_social, c.rut].some((v) => String(v || "").toLocaleLowerCase().includes(q));
  };
  const visibleAvailable = available.filter(matchesSearch);
  const availableListRef = useRef<HTMLDivElement>(null);
  const [availableHasOverflow, setAvailableHasOverflow] = useState(false);
  useEffect(() => {
    const element = availableListRef.current;
    if (!element) return;
    const updateOverflow = () => setAvailableHasOverflow(element.scrollWidth > element.clientWidth + 1);
    updateOverflow();
    window.addEventListener("resize", updateOverflow);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateOverflow);
    observer?.observe(element);
    return () => {
      window.removeEventListener("resize", updateOverflow);
      observer?.disconnect();
    };
  }, [visibleAvailable.length, search, selected.length]);
  const selectedItems = batch?.items.filter((i) => {
    const q = search.trim().toLocaleLowerCase();
    return !q || [i.razon_social, i.rut_receptor].some((v) => v.toLocaleLowerCase().includes(q));
  }) || [];
  const incompleteSelected = selected.filter((id) => {
    const client = clients.find((c) => c.id === id);
    return !amounts[id] || amounts[id] <= 0 || !(details[id]?.trim() || detailGlobal.trim() || detailText(client?.detalle_default));
  });
  const total = batch?.items.reduce((sum, i) => sum + i.monto, 0) || 0;
  const friendlyBatchError = (message: string) => message.startsWith("SiiValidationError") ? "No se pudo validar la vista previa en SII." : message;
  const create = async () => {
    try {
      setBusy(true);
      setError(null);
      setBatch(await batchesApi.create(period, fecha, detailGlobal, retentionMode));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const createNewBatch = async () => {
    try {
      setBusy(true);
      setError(null);
      const fresh = await batchesApi.create(periodNow, today, defaultGlobalDetail(periodNow), "issuer_handles_ppm");
      setPeriod(periodNow);
      setFecha(today);
      setDetailGlobal(defaultGlobalDetail(periodNow));
      setRetentionMode("issuer_handles_ppm");
      setSelected([]);
      setAmounts({});
      setDetails({});
      setPreview(null);
      setResult(null);
      setDirty(false);
      setSaveState("idle");
      setNewBatchConfirm(false);
      setBatch(fresh);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const requestNewBatch = () => {
    if (!batch || batch.status === "processing") return;
    const draftHasChanges = batch.status === "draft" && (batch.items.length > 0 || dirty);
    if (batch.status === "ready" || draftHasChanges) setNewBatchConfirm(true);
    else void createNewBatch();
  };
  const saveSettings = async () => {
    if (!batch || !editable || !dirty || busy) return false;
    try {
      setBusy(true);
      setSaveState("saving");
      setError(null);
      const updated = await batchesApi.update(batch.id, {
        fecha_emision: fecha,
        detalle_global: detailGlobal,
        retention_mode: retentionMode,
      });
      setBatch(updated);
      setDirty(false);
      setSaveState("saved");
      window.setTimeout(() => setSaveState("idle"), 2500);
      return true;
    } catch (e) {
      setError(e);
      setSaveState("idle");
      return false;
    } finally {
      setBusy(false);
    }
  };
  const addSelected = async () => {
    if (!batch) return;
    try {
      setBusy(true);
      setError(null);
      for (const id of selected) {
        const c = clients.find((x) => x.id === id)!;
        const detail = details[id]?.trim() || detailGlobal.trim() || detailText(c.detalle_default);
        if (!detail)
          throw new Error(`Ingresa el detalle de ${c.razon_social}.`);
        const amount = amounts[id];
        if (!amount || amount <= 0)
          throw new Error(`Ingresa un monto válido para ${c.razon_social}.`);
        if (retentionOverrides[id]) await batchesApi.addItem(batch.id, id, amount, detail, retentionOverrides[id]);
        else await batchesApi.addItem(batch.id, id, amount, detail);
      }
      setSelected([]);
      setBatch(await batchesApi.get(batch.id));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const prepare = async () => {
    if (!batch) return;
    try {
      setBusy(true);
      setError(null);
      if (dirty && !(await saveSettings())) return;
      setPreview(await batchesApi.prepare(batch.id));
      setBatch(await batchesApi.get(batch.id));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const emit = async (retry = false) => {
    if (!batch) return;
    try {
      setBusy(true);
      setError(null);
      setConfirm(false);
      const r = retry
        ? await batchesApi.retryFailed(batch.id)
        : await batchesApi.emit(batch.id);
      const latest = await batchesApi.get(batch.id);
      setBatch(latest);
      setResult({
        ...r,
        total: latest.items.length,
        success: latest.items.filter((item) => item.status === "success").length,
        failed: latest.items.filter((item) => item.status === "failed").length,
        unknown: latest.items.filter((item) => item.status === "emission_status_unknown").length,
        pending: latest.items.filter((item) => item.status === "pending").length,
        processing: latest.items.filter((item) => item.status === "processing").length,
        results: latest.items.map((item) => ({
          item_id: item.id,
          status: item.status,
          bhe_emission_id: item.bhe_emission_id,
          folio: item.folio,
          error_message: item.error_message,
        })),
      });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const retryItem = async () => {
    if (!batch || itemRetryId === null) return;
    const activeBatch = batch;
    try {
      setBusy(true);
      setError(null);
      setItemRetryId(null);
      setBatch({ ...activeBatch, status: "processing" });
      await batchesApi.retryItem(activeBatch.id, itemRetryId);
      const latest = await batchesApi.get(activeBatch.id);
      setBatch(latest);
      setResult(null);
    } catch (e) {
      setError(e);
      void batchesApi.get(activeBatch.id).then(setBatch).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };
  const editItem = async (id: number, monto: number, detalle: string, retention: RetentionMode | null) => {
    if (!batch) return;
    try {
      setError(null);
      await batchesApi.updateItem(batch.id, id, monto, detalle, retention);
      setBatch(await batchesApi.get(batch.id));
      setEditingItemId(null);
    } catch (e) {
      setError(e);
    }
  };
  const beginEditItem = (item: Batch["items"][number]) => {
    setEditingItemId(item.id);
    setEditDraft({
      monto: String(item.monto),
      detalle: detailText(item.detalle),
      retention: item.retention_mode_override || "",
    });
  };
  const removeItem = async (id: number) => {
    if (!batch) return;
    try {
      await batchesApi.removeItem(batch.id, id);
      setBatch(await batchesApi.get(batch.id));
      setEditingItemId(null);
    } catch (e) {
      setError(e);
    }
  };
  return (
    <section>
      <div className="page-head monthly-page-header">
        {batch && <button className="secondary new-batch-button" onClick={requestNewBatch} disabled={busy || batch.status === "processing"}>+ Nuevo lote</button>}
        <div>
          <p className="eyebrow">OPERACIÓN MENSUAL</p>
          <h1>Emisión mensual</h1>
        </div>
      </div>
      {batch && <p className="muted batch-identity">Lote #{batch.id} · {batch.status === "completed_with_errors" ? "Finalizado con errores" : batch.status === "completed" ? "Finalizado" : batch.status === "processing" ? "En procesamiento" : batch.status === "ready" ? "Preparado" : "Borrador"} · Proveedor: {batch.provider === "sii" ? "SII" : "SimpleFactura"}</p>}
      <div className="card global-options">
        <h2>{batch ? "Preparar lote" : "Opciones globales"}</h2>
        <div className="filters">
          <label>
            Período
            <span className="field-help">
              Mes al que corresponde este lote. No modifica la fecha tributaria de las boletas.
            </span>
            <input
              type="month"
              value={period}
              onChange={(e) => {
                const next = e.target.value;
                if (detailGlobal === defaultGlobalDetail(period))
                  setDetailGlobal(defaultGlobalDetail(next));
                setPeriod(next);
                setDirty(true);
              }}
            />
          </label>
          <label>
            Fecha de emisión
            <span className="field-help">
              Fecha que se utilizará al emitir las boletas en el SII.
            </span>
            <input
              type="date"
              value={fecha}
              onChange={(e) => {
                setFecha(e.target.value);
                setDirty(true);
              }}
              disabled={!!batch && !editable}
            />
          </label>
          <label className="global-detail">
            Detalle global
            <input
              value={detailGlobal}
              onChange={(e) => {
                setDetailGlobal(e.target.value);
                setDirty(true);
              }}
              disabled={!!batch && !editable}
            />
          </label>
          <RetentionSelector
            value={retentionMode}
            name="monthly-retention"
            variant="short"
            onChange={(value) => { setRetentionMode(value); setDirty(true); }}
          />
        </div>
      </div>
      <ErrorBox error={error} />
      {!batch && (
        <div className="card empty">
          <h2>Comienza el proceso mensual</h2>
          <p>
            Crea un borrador para seleccionar clientes y preparar sus boletas.
          </p>
          <button onClick={create} disabled={busy}>
            Crear batch draft
          </button>
        </div>
      )}
      {batch && (
        <>
          <div className="toolbar">
            <span className={`status ${batch.status}`}>{batchStatusLabel(batch.status)}</span>
            <span><b>Proveedor:</b> {batch.provider === "sii" ? "SII" : "SimpleFactura"}</span>
            <span>
              {batch.items.length} BHE · {money(total)}
            </span>
            {editable && (
              <>
                <button
                  className="secondary"
                  onClick={() => void saveSettings()}
                  disabled={busy || !dirty}
                >
                  {saveState === "saving"
                    ? "Guardando…"
                    : saveState === "saved"
                      ? "✓ Guardado"
                      : "Guardar cambios"}
                </button>
                {dirty && (
                  <span className="dirty-state">Cambios sin guardar</span>
                )}
              </>
            )}
          </div>
          {editable && (
            <div className="card add-box">
              <h2>Clientes disponibles</h2>
              <p className="muted">
                Aún no forman parte de este batch. Revisa el detalle que
                recibirán antes de agregarlos.
              </p>
              <div className="batch-tools">
                <input aria-label="Buscar clientes" placeholder="Buscar por nombre o RUT..." value={search} onChange={(e) => setSearch(e.target.value)} />
              </div>
              <div ref={availableListRef} className="available-clients compact-client-list" aria-label="Clientes disponibles para preparar">
                {visibleAvailable.map((c) => {
                  const displayDetail =
                    detailGlobal.trim() || detailText(c.detalle_default);
                  const eligible = isEligible(c);
                  const addClient = () => {
                    if (eligible && !busy) setSelected((s) => s.includes(c.id) ? s : [...s, c.id]);
                  };
                  return (
                    <div
                      className={!eligible ? "available-client disabled-client" : "available-client"}
                      key={c.id}
                      role="button"
                      tabIndex={eligible && !busy ? 0 : -1}
                      aria-disabled={!eligible || busy}
                      aria-label={`Agregar ${displayName(c)}`}
                      onClick={addClient}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          addClient();
                        }
                      }}
                    >
                      <div className="available-client-content">
                        <span className="client-name-line"><b>{displayName(c)}</b><span onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}><ClientInfoPopover client={c} detail={displayDetail || undefined} /></span></span>
                        <small>{eligible ? "Detalle: " + (displayDetail || "Pendiente") : "Faltan datos"}</small>
                      </div>
                    </div>
                  );
                })}
                {visibleAvailable.length === 0 && <p className="muted empty-list">No quedan clientes disponibles con esta búsqueda.</p>}
              </div>
              {availableHasOverflow && <p className="horizontal-scroll-hint" aria-live="polite">Desliza para ver más →</p>}
              {selected.length > 0 && (
                <div className="selected-editor">
                  <div className="section-title-row">
                    <div>
                      <h3>Preparar clientes seleccionados</h3>
                      <p className="muted section-help">Configura monto y excepciones antes de agregarlos al lote.</p>
                    </div>
                    <span className="muted">{selected.length} seleccionados</span>
                  </div>
                  {selected.map((id) => {
                    const c = clients.find((x) => x.id === id)!;
                    const suggestedDetail =
                      detailGlobal.trim() || detailText(c.detalle_default);
                    return (
                      <div className="inline-fields" key={id}>
                        <span className="preparing-client-name"><strong>{displayName(c)}</strong><ClientInfoPopover client={c} detail={details[id] || suggestedDetail || undefined} /></span>
                          <input
                          type="number"
                          min="1"
                          placeholder="Monto CLP"
                          value={amounts[id] || ""}
                          onChange={(e) =>
                            setAmounts((a) => ({
                              ...a,
                              [id]: Number(e.target.value),
                            }))
                          }
                        />
                        <select aria-label={`Retención ${c.razon_social}`} value={retentionOverrides[id] || ""} onChange={(e) => setRetentionOverrides((o) => ({ ...o, [id]: (e.target.value || null) as RetentionMode | null }))}>
                          <option value="">Usar configuración global</option>
                          <option value="issuer_handles_ppm">El emisor se encarga del PPM</option>
                          <option value="receiver_retains_ppm">El receptor retiene el PPM</option>
                        </select>
                        <input
                          aria-label={`Detalle ${c.razon_social}`}
                          placeholder="Detalle"
                          value={details[id] ?? suggestedDetail}
                          onChange={(e) =>
                            setDetails((d) => ({ ...d, [id]: e.target.value }))
                          }
                        />
                        <button
                          type="button"
                          className="icon-button danger-text"
                          title="Quitar de selección"
                          aria-label="Quitar de selección"
                          onClick={() => setSelected((current) => current.filter((selectedId) => selectedId !== id))}
                        >
                          🗑
                        </button>
                      </div>
                    );
                  })}
                  <button className="form-action-spacing" onClick={addSelected} disabled={busy || incompleteSelected.length > 0}>
                    Agregar {selected.length} clientes al lote
                  </button>
                </div>
              )}
            </div>
          )}
          <div className="card">
            <div className="table-head">
              <div>
                <h2>Clientes agregados</h2>
                <p className="muted section-help">
                  Resumen del lote. Edita un item solo cuando necesites un override.
                </p>
              </div>
              <span className="total">TOTAL {money(total)}</span>
            </div>
            {batch.items.length === 0 ? (
              <p className="muted">Aún no hay clientes en este batch.</p>
            ) : (
              <div className="table-wrap">
                <table className="batch-summary-table">
                  <thead>
                    <tr>
                      <th>Cliente</th>
                      <th>Detalle</th>
                      <th>Monto</th>
                      <th>Retención</th>
                      <th>Estado</th>
                      <th>Folio</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedItems.map((i) => (
                      <Fragment key={i.id}>
                      <tr>
                        <td data-label="Cliente"><span className="client-name-line"><b>{clients.find((c) => c.id === i.client_id) ? displayName(clients.find((c) => c.id === i.client_id)!) : i.razon_social}</b>{clients.find((c) => c.id === i.client_id) && <ClientInfoPopover client={clients.find((c) => c.id === i.client_id)!} detail={detailText(i.detalle)} retention={i.retention_mode_override ? "Personalizada" : "Global"} />}</span></td>
                        <td data-label="Detalle">{detailText(i.detalle)}</td>
                        <td data-label="Monto">{money(i.monto)}</td>
                        <td data-label="Retención">
                          <span>{i.retention_mode_override === "receiver_retains_ppm" ? "Receptor" : i.retention_mode_override === "issuer_handles_ppm" ? "Emisor" : retentionMode === "receiver_retains_ppm" ? "Receptor" : "Emisor"}</span>
                          {i.retention_mode_override && <small>Personalizada</small>}
                        </td>
                        <td data-label="Estado">
                          <span className={`status ${i.status}`}>
                            {batchStatusLabel(i.status)}
                          </span>
                          {i.error_message && (
                            <small className="error-text">
                              {friendlyBatchError(i.error_message)}
                            </small>
                          )}
                        </td>
                        <td data-label="Folio">{i.folio ?? "\u2014"}</td>
                        <td data-label="Acciones">
                          {editable && (
                            <>
                              <button
                                type="button"
                                className="icon-button"
                                title="Editar item"
                                aria-label="Editar item"
                                onClick={() => beginEditItem(i)}
                              >
                                ✎
                              </button>
                              <button
                                type="button"
                                className="icon-button danger-text"
                                title="Eliminar del lote"
                                aria-label="Eliminar del lote"
                                onClick={() => void removeItem(i.id)}
                              >
                                🗑
                              </button>
                            </>
                          )}
                          {i.retryable && (
                            <button
                              className="secondary"
                              onClick={() => setItemRetryId(i.id)}
                              disabled={busy || batch.status === "processing"}
                            >
                              Reintentar
                            </button>
                          )}
                        </td>
                      </tr>
                      {editingItemId === i.id && editable && (
                        <tr className="item-editor-row">
                          <td colSpan={7}>
                            <div className="row-editor">
                              <label>
                                Monto CLP
                                <input
                                  type="number"
                                  min="1"
                                  value={editDraft.monto}
                                  onChange={(e) => setEditDraft((draft) => ({ ...draft, monto: e.target.value }))}
                                />
                              </label>
                              <label>
                                Detalle
                                <input
                                  value={editDraft.detalle}
                                  onChange={(e) => setEditDraft((draft) => ({ ...draft, detalle: e.target.value }))}
                                />
                              </label>
                              <label>
                                Retención
                                <select
                                  value={editDraft.retention}
                                  onChange={(e) => setEditDraft((draft) => ({ ...draft, retention: (e.target.value || "") as RetentionMode | "" }))}
                                >
                                  <option value="">Usar configuración global</option>
                                  <option value="issuer_handles_ppm">Emisor</option>
                                  <option value="receiver_retains_ppm">Receptor</option>
                                </select>
                              </label>
                              <div className="actions">
                                <button type="button" className="secondary" onClick={() => setEditingItemId(null)}>Cancelar</button>
                                <button
                                  type="button"
                                  disabled={busy || !Number(editDraft.monto) || Number(editDraft.monto) <= 0 || !editDraft.detalle.trim()}
                                  onClick={() => void editItem(i.id, Number(editDraft.monto), editDraft.detalle, editDraft.retention || null)}
                                >
                                  Guardar cambios
                                </button>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          {editable && (
            <button
              className="primary wide"
              onClick={prepare}
              disabled={busy || batch.items.length === 0 || incompleteSelected.length > 0}
            >
              Revisar antes de emitir
            </button>
          )}
          {batch.status === "processing" && (
            <p className="notice">Emitiendo lote. No cierres AppBoletas mientras se está procesando.</p>
          )}
          {batch.status === "ready" && (
            <button
              className="primary wide"
              onClick={() => setConfirm(true)}
              disabled={busy}
            >
              {`Emitir ${batch.items.length} BHE en SII`}
            </button>
          )}
          {preview && (
            <div className="card preview">
              <h2>Vista previa</h2>
              <p>
                Período {preview.period} · {preview.cantidad} BHE ·{" "}
                <b>{money(preview.total)}</b>
              </p>
              <p>Retención: {preview.items.filter((i) => i.retention_mode === "issuer_handles_ppm").length} Emisor · {preview.items.filter((i) => i.retention_mode === "receiver_retains_ppm").length} Receptor</p>
              <div className="table-wrap"><table className="batch-summary-table"><thead><tr><th>Cliente</th><th>Monto</th><th>Retención</th></tr></thead><tbody>{preview.items.map((item) => <tr key={item.item_id}><td data-label="Cliente">{item.razon_social}</td><td data-label="Monto">{money(item.monto)}</td><td data-label="Retención">{item.retention_mode === "receiver_retains_ppm" ? "Receptor" : item.retention_mode === "issuer_handles_ppm" ? "Emisor" : "No registrado"}</td></tr>)}</tbody></table></div>
              <p className="notice">
                Esta acción todavía NO emite las boletas.
              </p>
              <button className="secondary" onClick={() => setPreview(null)}>
                Cerrar preview
              </button>
            </div>
          )}
        </>
      )}
      {newBatchConfirm && (
        <Confirm
          title="Crear nuevo lote"
          text={batch?.status === "ready"
            ? "Este lote está preparado pero aún no ha sido emitido. Se conservará sin cambios y se creará un nuevo lote."
            : "El lote actual todavía no ha sido emitido. Se conservará como borrador y se creará un nuevo lote."}
          onCancel={() => setNewBatchConfirm(false)}
          onConfirm={() => void createNewBatch()}
          busy={busy}
          confirmLabel="Crear nuevo lote"
        />
      )}
      {confirm && (
        <Confirm
          title={result ? "Confirmar reintento" : "Confirmar emisión en SII"}
          text={result
            ? `Se reintentará ${result.failed} boleta${result.failed === 1 ? "" : "s"} en SII.`
            : `Se emitirán ${batch?.items.length || 0} Boletas de Honorarios Electrónicas reales en el SII.`}
          onCancel={() => setConfirm(false)}
          onConfirm={() => void emit(!!result)}
          busy={busy}
          confirmLabel={result
            ? "Confirmar reintento en SII"
            : `Confirmar emisión de ${batch?.items.length || 0} BHE en SII`}
        />
      )}
      {itemRetryId !== null && batch && (
        <Confirm
          title="Confirmar retry individual"
          text="Se reintentará únicamente esta boleta en SII. Las demás boletas del lote no serán procesadas."
          onCancel={() => setItemRetryId(null)}
          onConfirm={() => void retryItem()}
          busy={busy}
          confirmLabel="Confirmar retry de 1 BHE en SII"
        />
      )}
    </section>
  );
}

type ManualForm = {
  rut_receptor: string;
  razon_social: string;
  giro: string;
  direccion: string;
  region: string;
  comuna: string;
  email: string;
  detalle: string;
  monto: string;
  fecha_emision: string;
  retention_mode: RetentionMode;
};
type ManualCommon = Pick<ManualForm, "retention_mode" | "direccion" | "region" | "comuna" | "detalle" | "fecha_emision">;
type ManualOverrides = Partial<ManualCommon>;
const blankManual = (): ManualForm => ({
  rut_receptor: "",
  razon_social: "",
  giro: "",
  direccion: "",
  region: "13",
  comuna: "",
  email: "",
  detalle: "",
  monto: "",
  fecha_emision: manualDateBounds().max,
  retention_mode: "issuer_handles_ppm",
});
const blankManualCommon = (): ManualCommon => ({
  retention_mode: "issuer_handles_ppm",
  direccion: "",
  region: "13",
  comuna: "",
  detalle: "",
  fecha_emision: manualDateBounds().max,
});
const manualCommonFromForm = (form: ManualForm): ManualCommon => ({
  retention_mode: form.retention_mode,
  direccion: form.direccion,
  region: form.region,
  comuna: form.comuna,
  detalle: form.detalle,
  fecha_emision: form.fecha_emision,
});
const buildEffectiveManualBheDraft = (form: ManualForm, common: ManualCommon, overrides: ManualOverrides): ManualForm => {
  return { ...form, ...common, ...overrides };
};
const MANUAL_DRAFT_KEY = "appboletas.manualDraft";
const MANUAL_NEW_LOT_KEY = "appboletas.manualNewLot";
const loadManualDraft = (): { form: ManualForm; common: ManualCommon; overrides: ManualOverrides; customExpanded: boolean; editingItemId: number | null } => {
  try {
    const saved = localStorage.getItem(MANUAL_DRAFT_KEY);
    if (!saved) return { form: blankManual(), common: blankManualCommon(), overrides: {}, customExpanded: false, editingItemId: null };
    const parsed = JSON.parse(saved);
    const legacyForm = parsed.form || parsed;
    const form = { ...blankManual(), ...legacyForm };
    const common = { ...blankManualCommon(), ...(parsed.common || manualCommonFromForm(form)) };
    const editingItemId = parsed.editingItemId ?? null;
    const legacyCustomized = parsed.customized ?? (editingItemId !== null || Boolean(form.razon_social || form.giro || form.email));
    let overrides: ManualOverrides = parsed.overrides || {};
    if (!parsed.overrides && editingItemId !== null) {
      overrides = Object.fromEntries((Object.keys(common) as Array<keyof ManualCommon>).map((key) => [key, form[key]])) as ManualOverrides;
    } else if (!parsed.overrides && legacyCustomized) {
      const defaults = blankManual();
      overrides = Object.fromEntries((Object.keys(common) as Array<keyof ManualCommon>)
        .filter((key) => form[key] !== common[key] && form[key] !== "" && form[key] !== defaults[key])
        .map((key) => [key, form[key]])) as ManualOverrides;
    }
    return { form, common, overrides, customExpanded: parsed.customExpanded ?? legacyCustomized, editingItemId };
  } catch { return { form: blankManual(), common: blankManualCommon(), overrides: {}, customExpanded: false, editingItemId: null }; }
};
function NewBheView() {
  const manualDates = manualDateBounds();
  const [initialDraft] = useState(loadManualDraft);
  const [form, setForm] = useState<ManualForm>(initialDraft.form);
  const [common, setCommon] = useState<ManualCommon>(initialDraft.common);
  const [overrides, setOverrides] = useState<ManualOverrides>(initialDraft.overrides);
  const [customExpanded, setCustomExpanded] = useState(initialDraft.customExpanded);
  const [editing, setEditing] = useState<number | null>(null);
  const [editingItemId, setEditingItemId] = useState<number | null>(initialDraft.editingItemId);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [queueLoaded, setQueueLoaded] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [retryConfirm, setRetryConfirm] = useState(false);
  const [editLotConfirm, setEditLotConfirm] = useState(false);
  const [applyCommonConfirm, setApplyCommonConfirm] = useState(false);
  const [newBatchConfirm, setNewBatchConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const update = (key: keyof ManualForm, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
    if (Object.prototype.hasOwnProperty.call(common, key)) setOverrides((current) => ({ ...current, [key]: value }));
  };
  const updateCommon = (key: keyof ManualCommon, value: string) => {
    setCommon((current) => ({ ...current, [key]: value }));
    if (!Object.prototype.hasOwnProperty.call(overrides, key)) setForm((current) => ({ ...current, [key]: value }));
  };
  const updateCommonRegion = (value: string) => {
    const nextComuna = getCommunesByRegion(value).includes(common.comuna) ? common.comuna : "";
    setCommon((current) => ({ ...current, region: value, comuna: nextComuna }));
    setForm((current) => ({
      ...current,
      ...(Object.prototype.hasOwnProperty.call(overrides, "region") ? {} : { region: value }),
      ...(Object.prototype.hasOwnProperty.call(overrides, "comuna") ? {} : { comuna: nextComuna }),
    }));
  };
  const total = batch?.items.reduce((sum, item) => sum + item.monto, 0) || 0;
  const queuedCount = batch?.items.length || 0;
  const hasInFlightItems = !!batch?.items.some((item) => item.status === "pending" || item.status === "processing");
  const terminal = !!batch && batch.status !== "processing" && !hasInFlightItems && ["completed", "completed_with_errors"].includes(batch.status);
  const retryableCount = batch?.items.filter((item) => item.status === "failed" && item.retryable).length || 0;
  const reviewRequiredCount = batch?.items.filter((item) => item.status === "emission_status_unknown" || (item.status === "failed" && !item.retryable)).length || 0;
  const unresolvedCount = batch?.items.filter((item) => item.status === "failed" || item.status === "emission_status_unknown").length || 0;
  useEffect(() => {
    void batchesApi.list().then(async (batches) => {
      const manual = batches.filter((candidate) => candidate.source === "manual").sort((a, b) => b.id - a.id);
      const active = manual.find((candidate) => ["ready", "processing"].includes(candidate.status));
      const previous = active || (localStorage.getItem(MANUAL_NEW_LOT_KEY) === "1" ? undefined : manual[0]);
      if (previous) {
        const restored = await batchesApi.get(previous.id);
        setBatch(restored);
        if (initialDraft.editingItemId !== null) {
          const index = restored.items.findIndex((item) => item.id === initialDraft.editingItemId && item.status === "pending");
          if (index >= 0) setEditing(index);
          else setEditingItemId(null);
        }
      }
      setQueueLoaded(true);
    }).catch(setError);
  }, []);
  useEffect(() => {
    if (!batch || batch.status !== "processing") return;
    const timer = window.setInterval(() => {
      void batchesApi.get(batch.id).then(setBatch).catch(setError);
    }, 1200);
    return () => window.clearInterval(timer);
  }, [batch?.id, batch?.status]);
  useEffect(() => {
    const hasDraft = Object.entries(form).some(([key, value]) => value !== (blankManual() as any)[key])
      || Object.entries(common).some(([key, value]) => value !== (blankManualCommon() as any)[key])
      || Object.keys(overrides).length > 0 || editingItemId !== null;
    if (hasDraft) localStorage.setItem(MANUAL_DRAFT_KEY, JSON.stringify({ form, common, overrides, customExpanded, editingItemId }));
    else localStorage.removeItem(MANUAL_DRAFT_KEY);
  }, [form, common, overrides, customExpanded, editingItemId]);
  const validateForm = async (event: FormEvent) => {
    event.preventDefault();
    if (!queueLoaded || busy) return;
    setError(null);
    const effectiveForm = buildEffectiveManualBheDraft(form, common, overrides);
    const required: Array<[keyof ManualForm, string]> = [
      ["rut_receptor", "RUT"], ["region", "regi\u00f3n"], ["direccion", "dirección"], ["comuna", "comuna"],
      ["detalle", "detalle"], ["monto", "monto"], ["fecha_emision", "fecha de emisión"],
    ];
    const missing = required.find(([key]) => !effectiveForm[key].trim());
    if (missing) { setError(new Error(`Completa ${missing[1]}.`)); return; }
    const amount = Number(effectiveForm.monto);
    if (!Number.isInteger(amount) || amount <= 0) { setError(new Error("El monto debe ser un entero mayor que 0.")); return; }
    if (!validRut(effectiveForm.rut_receptor)) { setError(new Error("El RUT receptor no es válido.")); return; }
    if (effectiveForm.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(effectiveForm.email)) { setError(new Error("El email no es válido.")); return; }
    const dateError = manualDateError(effectiveForm.fecha_emision);
    if (dateError) { setError(new Error(dateError)); return; }
    setBusy(true);
    try {
      const payload = payloadFor({ ...effectiveForm, monto: String(amount) });
      if (editing !== null && batch) {
        const itemId = batch.items[editing]?.id;
        if (!itemId) return;
        setBatch(await batchesApi.updateManualItem(batch.id, itemId, payload));
        setEditing(null);
        setEditingItemId(null);
      } else if (!batch) {
        setBatch(await batchesApi.createManual([payload]));
      } else {
        setBatch(await batchesApi.addManualItem(batch.id, payload));
      }
      localStorage.removeItem(MANUAL_DRAFT_KEY);
      localStorage.removeItem(MANUAL_NEW_LOT_KEY);
      setForm(blankManual());
      setOverrides({});
      setCustomExpanded(false);
      setError(null);
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  };
  const edit = (index: number) => {
    const item = batch?.items[index];
    if (!item || item.status !== "pending") return;
    const detail = detailText(item.detalle);
    const itemForm: ManualForm = { rut_receptor: item.rut_receptor, razon_social: item.razon_social, giro: item.giro || "", direccion: item.direccion, region: String(item.region), comuna: item.comuna, email: item.email || "", detalle: detail, monto: String(item.monto), fecha_emision: item.fecha_emision || batch.fecha_emision, retention_mode: item.retention_mode_override || "issuer_handles_ppm" };
    setForm(itemForm);
    setOverrides(manualCommonFromForm(itemForm));
    setCustomExpanded(true);
    setEditing(index); setEditingItemId(item.id); setError(null);
  };
  const remove = async (index: number) => {
    if (!batch) return;
    const item = batch.items[index];
    if (!item || item.status !== "pending") return;
    try {
      await batchesApi.removeItem(batch.id, item.id);
      if (batch.items.length === 1) {
        setBatch(null);
        localStorage.setItem(MANUAL_NEW_LOT_KEY, "1");
      } else setBatch({ ...batch, items: batch.items.filter((_, i) => i !== index) });
      if (editing === index) { setEditing(null); setEditingItemId(null); setForm(blankManual()); setOverrides({}); setCustomExpanded(false); }
    } catch (e) { setError(e); }
  };
  const payloadFor = (item: ManualForm): ManualBhePayload => ({
    rut_receptor: item.rut_receptor, razon_social: item.razon_social || undefined, giro: item.giro || undefined,
    direccion: item.direccion, region: Number(item.region), comuna: item.comuna, email: item.email || undefined,
    detalle: [{ descripcion: item.detalle, cantidad: 1, precio_unitario: Number(item.monto) }],
    fecha_emision: item.fecha_emision, retention_mode: item.retention_mode,
  });
  const processAll = async () => {
    if (busy || !queuedCount || !batch || batch.status !== "ready") return;
    setConfirm(false);
    const dateError = batch.items.map((item) => manualDateError(item.fecha_emision || batch.fecha_emision)).find(Boolean);
    if (dateError) {
      setError(new Error(dateError)); return;
    }
    setBusy(true); setError(null);
    try {
      const ready = batch;
      setBatch({ ...ready, status: "processing", items: ready.items.map((item) => ({ ...item, status: "pending" })) });
      await batchesApi.emit(ready.id);
      const latest = await batchesApi.get(ready.id);
      setBatch(latest);
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  };
  const retrySafeFailures = async () => {
    if (busy || !batch || !terminal || !retryableCount) return;
    const dateError = batch.items.filter((item) => item.retryable).map((item) => manualDateError(item.fecha_emision || batch.fecha_emision)).find(Boolean);
    if (dateError) { setRetryConfirm(false); setError(new Error(dateError)); return; }
    setBusy(true); setRetryConfirm(false); setError(null);
    setBatch({ ...batch, status: "processing", items: batch.items.map((item) => item.retryable ? { ...item, status: "pending" } : item) });
    try {
      await batchesApi.retryFailed(batch.id);
      setBatch(await batchesApi.get(batch.id));
    } catch (e) {
      setError(e);
      try { setBatch(await batchesApi.get(batch.id)); } catch { /* preserve the last visible state */ }
    } finally { setBusy(false); }
  };
  const cloneEditable = async () => {
    if (!batch || !terminal || !retryableCount || busy) return;
    setBusy(true); setError(null); setEditLotConfirm(false);
    try {
      const cloned = await batchesApi.cloneEditable(batch.id);
      const first = cloned.items[0];
      if (first) setCommon({
        retention_mode: first.retention_mode_override || "issuer_handles_ppm",
        direccion: first.direccion, region: String(first.region), comuna: first.comuna,
        detalle: detailText(first.detalle), fecha_emision: first.fecha_emision || cloned.fecha_emision,
      });
      setForm(blankManual()); setOverrides({}); setCustomExpanded(false);
      setEditing(null); setEditingItemId(null);
      localStorage.removeItem(MANUAL_NEW_LOT_KEY);
      setBatch(cloned);
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  };
  const applyCommon = async () => {
    if (!batch || batch.status !== "ready" || busy) return;
    setApplyCommonConfirm(false); setError(null);
    if (!common.direccion.trim() || !common.region || !common.comuna.trim() || !common.detalle.trim() || !common.fecha_emision) {
      setError(new Error("Completa los datos comunes antes de aplicarlos.")); return;
    }
    const dateError = manualDateError(common.fecha_emision);
    if (dateError) { setError(new Error(dateError)); return; }
    setBusy(true);
    try {
      setBatch(await batchesApi.applyManualCommon(batch.id, { ...common, region: Number(common.region) }));
      setEditing(null); setEditingItemId(null); setForm(blankManual()); setOverrides({}); setCustomExpanded(false);
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  };
  const requestNewBatch = () => {
    if (!terminal || busy) return;
    if (batch.items.some((item) => item.status === "failed" || item.status === "emission_status_unknown")) setNewBatchConfirm(true);
    else newBatch();
  };
  const newBatch = () => {
    setBatch(null); setForm(blankManual()); setOverrides({}); setCustomExpanded(false); localStorage.removeItem(MANUAL_DRAFT_KEY); localStorage.setItem(MANUAL_NEW_LOT_KEY, "1"); setEditing(null); setEditingItemId(null); setError(null); setConfirm(false); setRetryConfirm(false); setNewBatchConfirm(false);
  };
  const beginCustomization = () => {
    setForm((current) => buildEffectiveManualBheDraft(current, common, overrides));
    setCustomExpanded((value) => !value);
  };
  const rowItems = batch?.items || [];
  const showFolio = rowItems.some((item) => item.status === "success" || item.folio != null);
  const showActions = batch?.status === "ready" && rowItems.some((item) => item.status === "pending");
  const labelStatus = (status: string) => status === "success" ? "Emitida" : status === "failed" ? "Fallida" : status === "emission_status_unknown" ? "Revisión necesaria" : status === "processing" ? "Procesando..." : status === "pending" ? "Pendiente" : "Pendiente";
  return (
    <section>
      <div className="page-head"><div><p className="eyebrow">EMISIÓN INDIVIDUAL</p><h1>Nueva boleta</h1><p className="muted">Prepara boletas para receptores no recurrentes sin guardarlos como clientes frecuentes.</p></div></div>
      <ErrorBox error={error} />
      {(!batch || batch.status === "ready") && <>
        <section className="card form manual-common-card">
          <h2>Datos comunes del lote</h2>
          <div className="form-grid">
            <RetentionSelector value={common.retention_mode} name="manual-common-retention" variant="long" onChange={(value) => updateCommon("retention_mode", value)} />
            <label>Dirección<input value={common.direccion} onChange={(e) => updateCommon("direccion", e.target.value)} /></label>
            <LocationFields region={common.region} comuna={common.comuna} onRegionChange={updateCommonRegion} onComunaChange={(value) => updateCommon("comuna", value)} />
            <label>Detalle<input value={common.detalle} onChange={(e) => updateCommon("detalle", e.target.value)} /></label>
            <label>Fecha de emisión<input type="date" min={manualDates.min} max={manualDates.max} value={common.fecha_emision} onChange={(e) => updateCommon("fecha_emision", e.target.value)} /></label>
          </div>
          {batch?.status === "ready" && queuedCount > 0 && <div className="actions manual-form-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setApplyCommonConfirm(true)}>Aplicar datos comunes a {queuedCount} pendientes</button></div>}
        </section>
        <form className="card form manual-entry-card" onSubmit={validateForm}>
          <h2>{editing === null ? "Agregar boleta" : "Editar boleta preparada"}</h2>
          <div className="form-grid">
            <label>RUT receptor<input value={form.rut_receptor} onChange={(e) => update("rut_receptor", e.target.value)} required /></label>
            <label>Monto CLP<input type="number" min="1" step="1" value={form.monto} onChange={(e) => update("monto", e.target.value)} required /></label>
          </div>
          {editing === null && <button type="button" className="link manual-customize-toggle" aria-expanded={customExpanded} onClick={beginCustomization}>{customExpanded ? "▾ Personalizar esta boleta" : "▸ Personalizar esta boleta"}</button>}
          {(customExpanded || editing !== null) && <div className="form-grid manual-custom-fields">
            <label>Nombre / razón social (opcional)<input value={form.razon_social} onChange={(e) => update("razon_social", e.target.value)} /></label>
            <label>Giro (opcional)<input value={form.giro} onChange={(e) => update("giro", e.target.value)} /></label>
            <label>Dirección<input value={form.direccion} onChange={(e) => update("direccion", e.target.value)} required /></label>
            <label>Email (opcional)<input type="email" value={form.email} onChange={(e) => update("email", e.target.value)} /></label>
            <LocationFields region={form.region} comuna={form.comuna} onRegionChange={(value) => { update("region", value); if (!getCommunesByRegion(value).includes(form.comuna)) update("comuna", ""); }} onComunaChange={(value) => update("comuna", value)} />
            <label>Detalle<input value={form.detalle} onChange={(e) => update("detalle", e.target.value)} required /></label>
            <RetentionSelector value={form.retention_mode} name="manual-item-retention" variant="long" onChange={(value) => update("retention_mode", value)} />
            <label>Fecha de emisión<input type="date" min={manualDates.min} max={manualDates.max} value={form.fecha_emision} onChange={(e) => update("fecha_emision", e.target.value)} required /></label>
          </div>}
          <div className="actions manual-form-actions"><button type="submit" disabled={busy || !queueLoaded}>{editing === null ? "+ Agregar boleta" : "Guardar cambios"}</button>{editing !== null && <button type="button" className="secondary" onClick={() => { setEditing(null); setEditingItemId(null); setForm(blankManual()); setOverrides({}); setCustomExpanded(false); }}>Cancelar edición</button>}</div>
        </form>
      </>}      <div className="card">
        <div className="table-head"><div><h2>Boletas preparadas</h2><p className="muted">{queuedCount} boletas preparadas · Total {money(total)}</p></div></div>
        {!rowItems.length ? <p className="muted">Agrega una boleta para comenzar.</p> : <div className="table-wrap"><table className="batch-summary-table"><thead><tr><th>Receptor</th><th>RUT</th><th>Detalle</th><th>Monto</th><th>Retención</th><th>Fecha</th><th>Estado</th>{showFolio && <th>Folio</th>}{showActions && <th>Acciones</th>}</tr></thead><tbody>{rowItems.map((item, index) => {
          const mode = item.retention_mode_override;
          const isPending = batch?.status === "ready" && item.status === "pending";
          return <tr key={item.id}><td className="recipient-name" data-label="Receptor">{item.sii_resolved_recipient_name?.trim() || item.razon_social?.trim() || "Sin nombre"}</td><td data-label="RUT">{formatRut(item.rut_receptor)}</td><td data-label="Detalle">{detailText(item.detalle)}</td><td data-label="Monto">{money(item.monto)}</td><td data-label="Retención">{mode === "receiver_retains_ppm" ? "Receptor" : "Emisor"}</td><td data-label="Fecha">{item.fecha_emision || batch?.fecha_emision}</td><td data-label="Estado"><span className={`status ${item.status}`}>{batch?.status === "processing" && item.status === "pending" ? "Procesando..." : labelStatus(item.status)}</span>{item.error_message && <small className="error-text">{item.status === "emission_status_unknown" ? "Verifica el resultado directamente en SII antes de cualquier otra acción." : item.error_message}</small>}</td>{showFolio && <td data-label="Folio">{item.folio ?? "\u2014"}</td>}{showActions && <td data-label="Acciones">{isPending && <><button className="secondary" onClick={() => edit(index)}>Editar</button> <button className="secondary" onClick={() => void remove(index)}>Eliminar</button></>}</td>}</tr>;
        })}</tbody></table></div>}
      </div>
      {batch?.status === "ready" && <button className="primary wide" onClick={() => setConfirm(true)}>Procesar todas</button>}
      {terminal && reviewRequiredCount > 0 && <p className="notice" role="status">Revisión manual requerida para {reviewRequiredCount} {reviewRequiredCount === 1 ? "boleta" : "boletas"}. Estos items no se reintentarán.</p>}
      {terminal && <div className="actions">
        <button onClick={() => { window.history.pushState({}, "", "/history"); window.dispatchEvent(new PopStateEvent("popstate")); }}>Ir a Historial</button>
        {retryableCount > 0 && <button className="secondary" onClick={() => setRetryConfirm(true)}>Reintentar fallidos</button>}
        {retryableCount > 0 && <button className="secondary" onClick={() => setEditLotConfirm(true)}>Editar lote</button>}
        <button className="secondary" onClick={requestNewBatch}>+ Nuevo lote</button>
      </div>}
      {terminal && retryableCount > 0 && <p className="muted">Reintentar fallidos usa los mismos datos. Editar lote crea una copia para corregirlos.</p>}
      {editLotConfirm && batch && <Confirm
        title="Editar lote"
        text={`Se creará un nuevo lote a partir de ${retryableCount} boletas fallidas seguras de reintentar. El lote original permanecerá sin cambios.`}
        onCancel={() => setEditLotConfirm(false)}
        onConfirm={() => void cloneEditable()}
        busy={busy}
        confirmLabel={`Editar ${retryableCount} boletas`}
      />}
      {applyCommonConfirm && batch && <Confirm
        title="Aplicar datos comunes"
        text={`Se actualizarán los datos comunes de ${queuedCount} boletas pendientes. RUT, monto y datos opcionales se conservarán.`}
        onCancel={() => setApplyCommonConfirm(false)}
        onConfirm={() => void applyCommon()}
        busy={busy}
        confirmLabel={`Aplicar a ${queuedCount} pendientes`}
      />}
      {retryConfirm && batch && <Confirm
        title="Confirmar reintento"
        text={`Se reintentarán ${retryableCount} ${retryableCount === 1 ? "boleta fallida" : "boletas fallidas"} que no presentan evidencia de emisión. Los items con estado incierto o posible emisión no serán procesados nuevamente.`}
        onCancel={() => setRetryConfirm(false)}
        onConfirm={() => void retrySafeFailures()}
        busy={busy}
        confirmLabel={`Reintentar ${retryableCount} ${retryableCount === 1 ? "boleta" : "boletas"}`}
      />}
      {newBatchConfirm && batch && <Confirm
        title="Comenzar un nuevo lote"
        text={`Este lote contiene ${unresolvedCount} ${unresolvedCount === 1 ? "boleta fallida o con estado incierto" : "boletas fallidas o con estado incierto"}. El lote permanecerá guardado y podrás consultarlo posteriormente. ¿Quieres comenzar un nuevo lote?`}
        onCancel={() => setNewBatchConfirm(false)}
        onConfirm={newBatch}
        busy={busy}
        confirmLabel="Nuevo lote"
      />}
      {confirm && <Confirm title="Confirmar emisión" text={`Se emitirán ${queuedCount} boletas por un total de ${money(total)}. Una vez emitidas en el SII no pueden deshacerse desde AppBoletas.`} onCancel={() => setConfirm(false)} onConfirm={() => void processAll()} busy={busy} confirmLabel={`Emitir ${queuedCount} boletas`} />}
    </section>
  );
}

function validRut(value: string) {
  const normalized = value.replace(/[.\s-]/g, "").toUpperCase();
  if (!/^\d{7,8}[0-9K]$/.test(normalized)) return false;
  const body = normalized.slice(0, -1);
  let factor = 2, sum = 0;
  for (let i = body.length - 1; i >= 0; i--) { sum += Number(body[i]) * factor; factor = factor === 7 ? 2 : factor + 1; }
  const remainder = 11 - (sum % 11);
  const digit = remainder === 11 ? "0" : remainder === 10 ? "K" : String(remainder);
  return digit === normalized.slice(-1);
}

function ClientForm({
  initial,
  onDone,
  onCancel,
}: {
  initial?: Client;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState({
    rut: initial?.rut || "",
    razon_social: initial?.razon_social || "",
    nombre_fantasia: initial?.nombre_fantasia || "",
    giro: initial?.giro || "",
    direccion: initial?.direccion || "",
    region: String(initial?.region || 13),
    comuna: initial?.comuna || "",
    email: initial?.email || "",
    detalle: detailText(initial?.detalle_default),
  });
  const [error, setError] = useState<unknown>(null);
  const update = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!form.razon_social.trim()) {
      setError(new Error("Para guardar este receptor como cliente debes ingresar su razón social."));
      return;
    }
    try {
      const payload: ClientPayload = {
        rut: form.rut,
        razon_social: form.razon_social,
        nombre_fantasia: form.nombre_fantasia || null,
        giro: form.giro || null,
        direccion: form.direccion,
        region: Number(form.region),
        comuna: form.comuna,
        email: form.email || null,
        detalle_default: form.detalle ? [{ descripcion: form.detalle }] : null,
      };
      if (initial) await clientsApi.update(initial.id, payload);
      else await clientsApi.create(payload);
      onDone();
    } catch (x) {
      setError(x);
    }
  };
  return (
    <form className="card form" onSubmit={submit}>
      <ErrorBox error={error} />
      <div className="form-grid">
        {(
          [
            "rut",
            "razon_social",
            "nombre_fantasia",
            "giro",
            "direccion",
            "email",
            "detalle",
          ] as const
        ).map((k) => (
          <label key={k}>
            {
              (
                {
                  rut: "RUT",
                  razon_social: "Razón social / Nombre",
                  nombre_fantasia: "Nombre de fantasía (opcional)",
                  giro: "Giro (opcional)",
                  direccion: "Dirección",
                  email: "Email (opcional)",
                  detalle: "Detalle predeterminado (opcional)",
                } as Record<string, string>
              )[k]
            }
            <input
              value={form[k]}
              onChange={(e) => update(k, e.target.value)}
              required={["rut", "razon_social", "direccion"].includes(
                k,
              )}
              aria-required={["rut", "razon_social", "direccion"].includes(k)}
            />
          </label>
        ))}
        <LocationFields
          region={form.region}
          comuna={form.comuna}
          onRegionChange={(value) => {
            update("region", value);
            if (!getCommunesByRegion(value).includes(form.comuna)) update("comuna", "");
          }}
          onComunaChange={(value) => update("comuna", value)}
        />
      </div>
      <div className="actions form-action-spacing">
        <button type="button" className="secondary" onClick={onCancel}>
          Cancelar
        </button>
        <button type="submit">Guardar cliente</button>
      </div>
    </form>
  );
}
function ClientsView() {
  const [list, setList] = useState<Client[]>([]),
    [q, setQ] = useState(""),
    [editing, setEditing] = useState<Client | null | undefined>(undefined),
    [error, setError] = useState<unknown>(null);
  const load = async () => {
    try {
      setList(await clientsApi.list());
    } catch (e) {
      setError(e);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const filtered = useMemo(
    () =>
      list.filter((c) =>
        `${c.razon_social} ${c.nombre_fantasia || ""} ${c.rut}`
          .toLowerCase()
          .includes(q.toLowerCase()),
      ),
    [list, q],
  );
  if (editing !== undefined)
    return (
      <section>
        <div className="page-head">
          <div>
            <p className="eyebrow">DATOS MAESTROS</p>
            <h1>{editing ? "Editar cliente" : "Nuevo cliente"}</h1>
          </div>
        </div>
        <ClientForm
          initial={editing || undefined}
          onDone={() => {
            setEditing(undefined);
            void load();
          }}
          onCancel={() => setEditing(undefined)}
        />
      </section>
    );
  return (
    <section>
      <div className="page-head clients-page-header">
        <div>
          <p className="eyebrow">DATOS MAESTROS</p>
          <h1>Clientes frecuentes</h1>
        </div>
        <button className="primary new-client-button" onClick={() => setEditing(null)}>
          Nuevo cliente
        </button>
      </div>
      <ErrorBox error={error} />
      <div className="card">
        <input
          className="search"
          placeholder="Buscar por nombre o RUT"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="table-wrap clients-table">
          <table>
            <thead>
              <tr>
                <th>Nombre</th>
                <th>RUT</th>
                <th>Dirección</th>
                <th>Detalle predeterminado</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <tr key={c.id}>
                        <td>
                    <b>{c.nombre_fantasia || c.razon_social}</b>
                    {c.nombre_fantasia && <small>{c.razon_social}</small>}
                    <small>{c.email}</small>
                  </td>
                  <td>{c.rut}</td>
                  <td>
                    {c.direccion}, {c.comuna}
                  </td>
                  <td>{detailText(c.detalle_default) || "—"}</td>
                  <td>
                    <button className="link" onClick={() => setEditing(c)}>
                      Editar
                    </button>
                    <button
                      className="link danger-text"
                      onClick={async () => {
                        if (confirm(`¿Desactivar ${c.razon_social}?`)) {
                          await clientsApi.deactivate(c.id);
                          void load();
                        }
                      }}
                    >
                      Desactivar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="clients-mobile-list" aria-label="Lista de clientes">
          {filtered.map((c) => (
            <article className="client-mobile-card" key={c.id}>
              <div className="client-mobile-heading">
                <div className="client-mobile-name">
                  <b>{c.nombre_fantasia || c.razon_social}</b>
                  {c.nombre_fantasia && <small>{c.razon_social}</small>}
                </div>
                <ClientInfoPopover client={c} />
              </div>
              <dl className="mobile-card-details">
                <div><dt>RUT</dt><dd>{c.rut}</dd></div>
                <div><dt>Dirección</dt><dd>{c.direccion}, {c.comuna}</dd></div>
                <div><dt>Detalle predeterminado</dt><dd>{detailText(c.detalle_default) || "—"}</dd></div>
              </dl>
              <div className="mobile-card-actions">
                <button className="link" onClick={() => setEditing(c)}>Editar</button>
                <button
                  className="link danger-text"
                  onClick={async () => {
                    if (confirm(`¿Desactivar ${c.razon_social}?`)) {
                      await clientsApi.deactivate(c.id);
                      void load();
                    }
                  }}
                >
                  Desactivar
                </button>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function HistoryView() {
  const [items, setItems] = useState<HistorySummary[]>([]),
    [detail, setDetail] = useState<HistoryDetail | null>(null),
    [expandedHistoryId, setExpandedHistoryId] = useState<number | null>(null),
    [status, setStatus] = useState(""),
    [rut, setRut] = useState(""),
    [from, setFrom] = useState(""),
    [to, setTo] = useState(""),
    [error, setError] = useState<unknown>(null);
  const load = async () => {
    try {
      const p = new URLSearchParams();
      if (status) p.set("status", status);
      if (rut) p.set("rut_receptor", rut);
      if (from) p.set("date_from", from);
      if (to) p.set("date_to", to);
      setItems(await historyApi.list(p));
    } catch (e) {
      setError(e);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    if (expandedHistoryId !== null && !items.some((item) => item.id === expandedHistoryId)) {
      setExpandedHistoryId(null);
    }
  }, [items, expandedHistoryId]);
  return (
    <section>
      <div className="page-head">
        <div>
          <p className="eyebrow">REGISTRO</p>
          <h1>Historial BHE</h1>
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="card filters-row">
        <label className="filter-field">
          Estado
          <select aria-label="Estado" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Todos los estados</option>
          <option value="success">Exitosas</option>
          <option value="failed">Fallidas</option>
              <option value="pending">Pendientes</option>
              <option value="awaiting_final_confirmation">Pendientes de confirmación SII</option>
          </select>
        </label>
        <label className="filter-field">
          RUT receptor
          <input
            aria-label="RUT receptor"
          placeholder="RUT receptor"
          value={rut}
          onChange={(e) => setRut(e.target.value)}
          />
        </label>
        <label className="filter-field">
          Desde
          <input
            aria-label="Desde"
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label className="filter-field">
          Hasta
          <input aria-label="Hasta" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <button onClick={() => void load()}>Filtrar</button>
      </div>
      <div className="card table-wrap history-table">
        <table>
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Receptor</th>
              <th>RUT</th>
              <th>Monto</th>
                      <th>Proveedor</th>
                      <th>Estado</th>
              <th>Folio</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr
                key={i.id}
                className="clickable"
                onClick={() =>
                  historyApi.get(i.id).then(setDetail).catch(setError)
                }
              >
                <td>{new Date(i.created_at).toLocaleDateString("es-CL")}</td>
                <td>
                  <span className="client-name-line">
                    <b>{i.nombre_fantasia?.trim() || i.razon_social}</b>
                    <ClientInfoPopover client={historyClient(i)} detail={detailText(i.detalle)} />
                  </span>
                </td>
                <td>{i.rut_receptor}</td>
                <td>{money(i.monto_total)}</td>
                      <td>{i.provider === "sii" ? "SII" : "SimpleFactura"}</td>
                      <td>
                      <span className={`status ${i.status}`}>{historyStatusLabel(i.status)}</span>
                </td>
                <td>{i.folio || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="history-mobile-list" aria-label="Lista de historial BHE">
        {items.map((i) => (
          <div
            key={i.id}
            className="history-mobile-card"
          >
            <div className="history-mobile-summary-row">
            <button
              type="button"
              className="history-mobile-summary"
              aria-expanded={expandedHistoryId === i.id}
              aria-controls={`history-panel-${i.id}`}
              onClick={() => setExpandedHistoryId((current) => current === i.id ? null : i.id)}
            >
              <span className="history-mobile-primary">
                <b>{i.nombre_fantasia?.trim() || i.razon_social}</b>
                <span>{new Date(i.created_at).toLocaleDateString("es-CL")} · {money(i.monto_total)}</span>
              </span>
              <span className="history-mobile-status"><span className={`status ${i.status}`}>{historyStatusLabel(i.status)}</span><span aria-hidden="true" className="history-mobile-chevron">›</span></span>
            </button>
            <ClientInfoPopover client={historyClient(i)} detail={detailText(i.detalle)} />
            </div>
            {expandedHistoryId === i.id && <div id={`history-panel-${i.id}`} className="history-mobile-panel">
            <dl className="mobile-card-details">
              <div><dt>RUT receptor</dt><dd>{i.rut_receptor}</dd></div>
              <div><dt>Proveedor</dt><dd>{i.provider === "sii" ? "SII" : "SimpleFactura"}</dd></div>
              <div><dt>Folio</dt><dd>{i.folio || "—"}</dd></div>
            </dl>
            <button className="link history-detail-action" type="button" onClick={() => historyApi.get(i.id).then(setDetail).catch(setError)}>Abrir detalle</button>
            </div>}
          </div>
        ))}
      </div>
      {detail && (
        <div className="card detail">
          <button className="link" onClick={() => setDetail(null)}>
            Cerrar detalle
          </button>
          <h2>{detail.razon_social}</h2>
          <p>
            {detail.rut_receptor} · {money(detail.monto_total)} ·{" "}
            {detail.status}
          </p>
          {detail.pdf_base64 && (
            <button onClick={() => openPdf(detail.pdf_base64!)}>Ver PDF</button>
          )}
          <pre>
            {JSON.stringify(
              {
                detalle: detail.detalle,
                folio: detail.folio,
                fecha: detail.fecha_emision_provider,
                codigo_barras: detail.codigo_barras,
              },
              null,
              2,
            )}
          </pre>
        </div>
      )}
    </section>
  );
}

function historyStatusLabel(status: string): string {
  return ({
    awaiting_final_confirmation: "Pendiente de confirmación en SII",
    finalizing: "Emitiendo",
    emission_status_unknown: "Estado por verificar en SII",
    success: "Emitida",
    failed: "Fallida",
    pending: "Pendiente",
  } as Record<string, string>)[status] || status;
}

function maskRut(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length < 5) return "••••";
  return `${"•".repeat(Math.max(0, trimmed.length - 4))}${trimmed.slice(-4)}`;
}

function retentionLabel(mode: RetentionMode): string {
  return mode === "receiver_retains_ppm"
    ? "El receptor retiene el PPM"
    : "El emisor se encarga del PPM";
}

const initialTheme = (): "light" | "dark" => {
  const saved = localStorage.getItem("appboletas-theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
};
export default function App() {
  const route = () =>
    window.location.pathname === "/clients"
      ? "clients"
      : window.location.pathname === "/history"
        ? "history"
        : window.location.pathname === "/new-bhe"
          ? "new"
          : "month";
  const [view, setView] = useState(route);
  const [theme, setTheme] = useState<"light" | "dark">(initialTheme);
  const [navOpen, setNavOpen] = useState(false);
  const go = (v: string) => {
    const path =
      v === "clients"
        ? "/clients"
        : v === "history"
          ? "/history"
          : v === "new"
            ? "/new-bhe"
            : "/";
    window.history.pushState({}, "", path);
    setView(v);
    setNavOpen(false);
  };
  useEffect(() => {
    const f = () => setView(route());
    window.addEventListener("popstate", f);
    return () => window.removeEventListener("popstate", f);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("appboletas-theme", theme);
  }, [theme]);
  useEffect(() => {
    if (!navOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setNavOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [navOpen]);
  return (
    <div className="app">
      <header className="app-header">
        <button
          type="button"
          className="menu-toggle"
          aria-label="Abrir menú"
          aria-controls="app-navigation"
          aria-expanded={navOpen}
          onClick={() => setNavOpen(true)}
        >
          ☰
        </button>
        <strong>AppBoletas</strong>
        <span className="app-context">{view === "clients" ? "Clientes" : view === "history" ? "Historial" : view === "new" ? "Nueva boleta" : "Emisión mensual"}</span>
      </header>
      <Nav
        view={view}
        go={go}
        theme={theme}
        toggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        open={navOpen}
        onClose={() => setNavOpen(false)}
      />
      {navOpen && <button type="button" className="drawer-backdrop" aria-label="Cerrar menú" onClick={() => setNavOpen(false)} />}
      <main>
        {view === "clients" ? (
          <ClientsView />
        ) : view === "history" ? (
          <HistoryView />
        ) : view === "new" ? (
          <NewBheView />
        ) : (
          <MonthView />
        )}
      </main>
    </div>
  );
}
