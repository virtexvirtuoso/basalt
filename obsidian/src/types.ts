/**
 * Shared types — mirrors the schemas emitted by basalt-vault's serialize.py.
 * Keep in sync with src/basalt/serialize.py (SCHEMA_VERSION = 1).
 */

export interface FalsificationRule {
  kind: string;
  params: Record<string, unknown>;
  text: string;
}

export interface BuriedInsightFinding {
  verb: "buried-insight";
  schema: number;
  rel_path: string;
  title: string;
  stem: string;
  created: string | null;
  updated: string | null;
  word_count: number;
  score: number;
  hub_density: number;
  hub_penalty: number;
  inbound_recent_count: number;
  quote: string;
  quote_provenance: string;
  vault_age_days: number;
  thresholds: Record<string, number>;
  validators: Array<{
    rel_path: string;
    title: string;
    updated: string | null;
    explicit_link: boolean;
    similarity: number;
  }>;
  falsification: FalsificationRule[];
}

export interface PairFinding {
  verb: "connection" | "contradiction";
  schema: number;
  similarity?: number;
  topical_similarity?: number;
  contradiction_score?: number;
  score: number;
  signals?: string[];
  version?: string; // e.g. "v0-heuristic" for contradiction
  note_a: { rel_path: string; title: string; quote: string; quote_provenance: string; hub_density?: number };
  note_b: { rel_path: string; title: string; quote: string; quote_provenance: string; hub_density?: number };
  falsification: FalsificationRule[];
}

export interface TrackRecord {
  schema: number;
  window_days: number;
  confirmed: number;
  pending: number;
  falsified: number;
  total: number;
  confirmed_pct: number;
  falsified_pct: number;
}

export interface BriefResponse {
  schema: number;
  section: string;
  track_record: TrackRecord;
  findings: {
    buried_insight?: BuriedInsightFinding[];
    connection?: PairFinding[];
    contradiction?: PairFinding[];
  };
}

export interface AuditVerdict {
  schema: number;
  brief_id: number;
  verb: string;
  finding_key: string;
  rule_kind: string;
  new_status: "confirmed" | "falsified" | "pending";
  reason: string;
  age_days: number;
}

export interface AuditResponse {
  schema: number;
  verb: "audit";
  verdicts: AuditVerdict[];
  track_record: TrackRecord;
}
