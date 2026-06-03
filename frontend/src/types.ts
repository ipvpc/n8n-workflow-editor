export type SettingsMeta = {
  base_url: string | null;
  api_key_masked: string | null;
  has_api_key: boolean;
  source: string;
  instance_id?: string | null;
  instance_name?: string | null;
};

export type WorkflowRow = {
  id: string;
  name?: string;
  active?: boolean;
  is_dirty?: boolean;
  synced_at?: string | null;
  local_updated_at?: string | null;
};

export type WorkflowBackupRow = {
  id: string;
  name: string | null;
  label: string | null;
  source: string;
  created_at: string | null;
};

export type N8nInstanceRow = {
  id: string;
  name: string;
  base_url: string;
  api_key_masked: string;
  http_timeout_seconds: number;
  skip_tls_verify: boolean;
};

export type Preferences = {
  active_n8n_instance_id: string | null;
  active_llm_profile_id: string | null;
};

export type LlmProfileRow = {
  id: string;
  name: string;
  provider: "azure_openai" | "openai_compatible";
  config_public: Record<string, unknown>;
};
