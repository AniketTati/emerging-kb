"use client";

/**
 * Dedicated entity profile page — /explore/entity/[id].
 *
 * Distinct from the inline accordion on /explore in three ways:
 *   1. Deep-linkable / shareable URL (the inline view is hidden inside
 *      result list state).
 *   2. Full-width layout — Related buckets shown side-by-side instead
 *      of a vertical accordion, with all metadata visible at once.
 *   3. Back-link to /explore preserves the user's previous filters via
 *      the URL state the parent page maintains.
 *
 * Uses the same backend contract: GET /explore/entity/{id}/profile.
 * No new endpoints needed.
 */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft, FileText, DollarSign, Mail, User, Users, Building,
  AlertCircle, AlertOctagon, FileQuestion, Loader2, Pencil, Check, X,
  type LucideIcon,
} from "lucide-react";
import {
  getEntityProfile, renameEntity,
  type EntityProfile, type EntityProfileBucket,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";


const BUCKET_ICONS: Record<string, LucideIcon> = {
  "file-text":     FileText,
  "dollar-sign":   DollarSign,
  "mail":          Mail,
  "user":          User,
  "users":         Users,
  "building":      Building,
  "alert-circle":  AlertCircle,
  "alert-octagon": AlertOctagon,
  "blueprint":     FileText,
  "sticky-note":   FileText,
  "file":          FileQuestion,
};


function bucketHref(bucket: EntityProfileBucket): string {
  const params = new URLSearchParams();
  if (bucket.deep_link_kind) params.set("kind", bucket.deep_link_kind);
  if (bucket.deep_link_doc_type) {
    params.set("doc_types", bucket.deep_link_doc_type);
  }
  if (bucket.deep_link_q) params.set("q", bucket.deep_link_q);
  return `/explore?${params.toString()}`;
}


export default function EntityProfilePage() {
  const params = useParams<{ id: string }>();
  const entityId = params.id;
  const [profile, setProfile] = useState<EntityProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!entityId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getEntityProfile(entityId)
      .then((p) => { if (!cancelled) setProfile(p); })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load profile");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [entityId]);

  return (
    <div className="flex h-full">
      <Sidebar current="explore" />
      <main className="flex-1 flex flex-col min-w-0 bg-zinc-50/40">
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3 bg-white">
          <Link
            href="/explore"
            className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-900"
            data-testid="entity-profile-back"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to Explore
          </Link>
          <span className="text-zinc-300">/</span>
          <span className="text-xs text-zinc-700">
            Entity profile
          </span>
          <span className="ml-auto text-[10px] mono text-zinc-400">
            {entityId.slice(0, 8)}…
          </span>
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="max-w-5xl mx-auto px-8 py-8">
            {loading && (
              <div className="flex items-center justify-center py-20 text-zinc-400">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            )}
            {error && (
              <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                {error.includes("404")
                  ? "This entity does not exist (or was deleted)."
                  : `Failed to load: ${error}`}
              </div>
            )}
            {profile && (
              <ProfileBody profile={profile} />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}


function ProfileBody({ profile }: { profile: EntityProfile }) {
  // Local override so the rename change is reflected immediately without
  // a full /profile refetch. Falls back to the prop on first paint.
  const [name, setName] = useState(profile.canonical_name);
  useEffect(() => { setName(profile.canonical_name); }, [profile.canonical_name]);

  return (
    <div className="space-y-6" data-testid="entity-profile">
      {/* Header */}
      <div className="rounded-lg border border-zinc-200 bg-white p-6">
        <div className="flex items-start gap-3">
          <span className="text-[11px] mono px-2 py-1 rounded bg-zinc-100 text-zinc-700">
            {profile.entity_type}
          </span>
          <div className="flex-1 min-w-0">
            <EditableCanonicalName
              entityId={profile.id}
              value={name}
              onChange={setName}
            />
            {profile.summary && (
              <p className="mt-2 text-sm text-zinc-600 leading-relaxed">
                {profile.summary}
              </p>
            )}
          </div>
        </div>

        {/* Metadata strip */}
        <div className="mt-5 pt-4 border-t border-zinc-100 grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-3 text-[11px]">
          <Stat label="Documents" value={profile.n_docs.toString()} />
          <Stat label="Mentions" value={profile.mention_count.toString()} />
          <Stat
            label="First seen"
            value={profile.first_seen?.slice(0, 10) ?? "—"}
          />
          <Stat
            label="Last seen"
            value={profile.last_seen?.slice(0, 10) ?? "—"}
          />
        </div>

        {profile.aliases.length > 0 && (
          <div className="mt-4 pt-4 border-t border-zinc-100">
            <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">
              Aliases
            </div>
            <div className="flex flex-wrap gap-1.5">
              {profile.aliases.map((a) => (
                <span
                  key={a}
                  className="text-[11px] mono px-2 py-0.5 rounded bg-zinc-100 text-zinc-700"
                >
                  {a}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Related buckets — grid layout (vs accordion on /explore) */}
      <div>
        <div className="text-xs uppercase tracking-wider text-zinc-500 mb-3">
          Related ({profile.related.length} buckets)
        </div>
        {profile.related.length === 0 ? (
          <div className="rounded-lg border border-dashed border-zinc-200 bg-white px-6 py-10 text-center text-sm text-zinc-500">
            No related buckets — this entity has no linked sub-entities,
            relationships, or co-occurring entities yet.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {profile.related.map((b) => (
              <BucketCard key={b.key} bucket={b} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}


/** Inline-editable canonical_name. Pencil → input + save/cancel.
 *  Persists via PATCH /entities/:id/canonical-name and reflects the
 *  new value in the parent immediately on success. */
function EditableCanonicalName({
  entityId,
  value,
  onChange,
}: {
  entityId: string;
  value: string;
  onChange: (next: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { setDraft(value); }, [value]);

  async function save() {
    const trimmed = draft.trim();
    if (!trimmed) {
      setErr("Name cannot be blank.");
      return;
    }
    if (trimmed === value) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const out = await renameEntity(entityId, trimmed);
      onChange(out.canonical_name);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Rename failed");
    } finally {
      setSaving(false);
    }
  }

  function cancel() {
    setDraft(value);
    setErr(null);
    setEditing(false);
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); save(); }
            if (e.key === "Escape") { e.preventDefault(); cancel(); }
          }}
          autoFocus
          disabled={saving}
          className="flex-1 text-2xl font-semibold text-zinc-900 leading-tight bg-white border-b-2 border-zinc-900 px-1 py-0.5 focus:outline-none disabled:opacity-50"
          data-testid="entity-canonical-input"
        />
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="p-1.5 rounded bg-zinc-900 text-white hover:bg-zinc-700 cursor-pointer disabled:opacity-50"
          data-testid="entity-canonical-save"
          aria-label="Save"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
        </button>
        <button
          type="button"
          onClick={cancel}
          disabled={saving}
          className="p-1.5 rounded text-zinc-500 hover:bg-zinc-100 cursor-pointer disabled:opacity-50"
          aria-label="Cancel"
        >
          <X className="w-4 h-4" />
        </button>
        {err && (
          <span className="text-xs text-red-600 mono ml-2" data-testid="entity-canonical-err">
            {err}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 group">
      <h1
        className="text-2xl font-semibold text-zinc-900 leading-tight"
        data-testid="entity-profile-name"
      >
        {value}
      </h1>
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="opacity-0 group-hover:opacity-100 transition-opacity p-1 text-zinc-400 hover:text-zinc-700 cursor-pointer"
        data-testid="entity-canonical-edit"
        title="Edit canonical name"
        aria-label="Edit canonical name"
      >
        <Pencil className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}


function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-0.5">
        {label}
      </div>
      <div className="text-sm text-zinc-900 mono">{value}</div>
    </div>
  );
}


function BucketCard({ bucket }: { bucket: EntityProfileBucket }) {
  const Icon = BUCKET_ICONS[bucket.icon] ?? FileText;
  return (
    <Link
      href={bucketHref(bucket)}
      className="block rounded-lg border border-zinc-200 bg-white p-4 hover:border-zinc-400 hover:bg-zinc-50/40 transition-colors"
      data-testid="entity-profile-bucket"
      data-bucket-key={bucket.key}
    >
      <div className="flex items-center gap-2">
        <Icon className="w-4 h-4 text-zinc-500" strokeWidth={1.75} />
        <span className="text-sm font-medium text-zinc-900">
          {bucket.label}
        </span>
        <span className="ml-auto text-[11px] mono text-zinc-500">
          {bucket.count}
        </span>
      </div>
      {bucket.subtitle && (
        <div className="mt-1.5 text-xs text-zinc-500 line-clamp-2">
          {bucket.subtitle}
        </div>
      )}
      <div className="mt-3 text-[11px] text-zinc-400 mono">
        view all →
      </div>
    </Link>
  );
}
