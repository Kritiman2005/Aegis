import type { IconType } from 'react-icons';
import {
  SiGmail, SiGoogledrive, SiGooglesheets, SiGoogledocs, SiNotion, SiFigma, SiHubspot, SiAirtable,
  SiLinear, SiJira, SiStripe, SiShopify, SiBrave, SiGooglemaps, SiZendesk,
  SiGithub, SiPostgresql, SiSentry, SiElasticsearch, SiSqlite, SiDuckdb,
  SiQdrant, SiGit, SiHuggingface, SiMongodb, SiDiscord, SiRedis, SiMysql, SiSupabase,
  SiAsana, SiTrello, SiClickup, SiConfluence, SiIntercom, SiGooglecloud,
} from 'react-icons/si';
import { FaSlack, FaSalesforce, FaAws } from 'react-icons/fa6';
import {
  Folder, Globe, Brain, Cpu, Clock, Cloud, Database, Layers, Wrench,
  FileText, FileType2, Presentation, FileSpreadsheet, AudioLines, ScanText,
  ArrowDownUp,
  type LucideIcon,
} from 'lucide-react';

// Split out of serviceIcons.tsx (which re-exports everything here) so this
// pure lookup logic can be unit-tested without Vite needing a JSX/React
// transform just to import a plain function — serviceIcons.tsx's own
// ServiceLogo component (the only JSX in the original file) is the only
// thing that still lives there.
export interface ServiceIconSpec {
  Icon: IconType | LucideIcon;
  color: string; // brand hex — real vendor color where a real logo exists, a neutral pick for generic fallbacks
}

// Keyed by the catalog/engine "name"/"id" fields the backend already sends
// (app.mcp.catalog's CONNECTORS_CATALOG keys, app.core.dbengines.registry's
// engine ids) — one lookup shared by MCPServersPanel and MarketplaceView so
// a given service always renders the same way everywhere.
const SERVICE_ICONS: Record<string, ServiceIconSpec> = {
  // MCP catalog — real brand marks wherever the vendor has one
  google_mail: { Icon: SiGmail, color: '#EA4335' },
  google_drive: { Icon: SiGoogledrive, color: '#1FA463' },
  google_sheets: { Icon: SiGooglesheets, color: '#0F9D58' },
  google_docs: { Icon: SiGoogledocs, color: '#4285F4' },
  slack: { Icon: FaSlack, color: '#4A154B' },
  notion: { Icon: SiNotion, color: '#000000' },
  figma: { Icon: SiFigma, color: '#F24E1E' },
  hubspot: { Icon: SiHubspot, color: '#FF7A59' },
  salesforce: { Icon: FaSalesforce, color: '#00A1E0' },
  airtable: { Icon: SiAirtable, color: '#18BFFF' },
  linear: { Icon: SiLinear, color: '#5E6AD2' },
  jira: { Icon: SiJira, color: '#0052CC' },
  stripe: { Icon: SiStripe, color: '#635BFF' },
  shopify: { Icon: SiShopify, color: '#95BF47' },
  brave_search: { Icon: SiBrave, color: '#FB542B' },
  google_maps: { Icon: SiGooglemaps, color: '#4285F4' },
  zendesk: { Icon: SiZendesk, color: '#03363D' },
  github: { Icon: SiGithub, color: '#181717' },
  postgres: { Icon: SiPostgresql, color: '#4169E1' },
  sentry: { Icon: SiSentry, color: '#362D59' },
  elasticsearch: { Icon: SiElasticsearch, color: '#005571' },
  git: { Icon: SiGit, color: '#F05032' },

  // MCP catalog entries with no real vendor mark to show (a reference/
  // utility server, not a branded product) — an honest topical icon
  // instead of inventing a logo that doesn't exist.
  filesystem: { Icon: Folder, color: '#F4A93B' },
  fetch: { Icon: Globe, color: '#4B9EF4' },
  memory: { Icon: Brain, color: '#B24BF4' },
  sequential_thinking: { Icon: Cpu, color: '#4BD1F4' },
  time: { Icon: Clock, color: '#6B7280' },

  // Database/vector engines (app.core.dbengines.registry ids)
  sqlite: { Icon: SiSqlite, color: '#003B57' },
  duckdb: { Icon: SiDuckdb, color: '#FFC825' },
  qdrant: { Icon: SiQdrant, color: '#DC244C' },
  // Website-selected connectors (see catalog.py's remote_entry_to_catalog_dict)
  mongodb: { Icon: SiMongodb, color: '#47A248' },
  aws_s3: { Icon: FaAws, color: '#FF9900' },
  discord: { Icon: SiDiscord, color: '#5865F2' },
  redis: { Icon: SiRedis, color: '#DC382D' },
  mysql: { Icon: SiMysql, color: '#4479A1' },
  supabase: { Icon: SiSupabase, color: '#3ECF8E' },
  asana: { Icon: SiAsana, color: '#F06A6A' },
  trello: { Icon: SiTrello, color: '#0052CC' },
  clickup: { Icon: SiClickup, color: '#7B68EE' },
  confluence: { Icon: SiConfluence, color: '#172B4D' },
  intercom: { Icon: SiIntercom, color: '#1F8DED' },

  // AWS Labs official servers (github.com/awslabs/mcp) — react-icons has no
  // per-service AWS marks, so every AWS entry shares the same real AWS logo.
  aws_dynamodb: { Icon: FaAws, color: '#FF9900' },
  aws_lambda: { Icon: FaAws, color: '#FF9900' },
  aws_ecs: { Icon: FaAws, color: '#FF9900' },
  aws_eks: { Icon: FaAws, color: '#FF9900' },
  aws_redshift: { Icon: FaAws, color: '#FF9900' },
  aws_cloudwatch: { Icon: FaAws, color: '#FF9900' },
  aws_iam: { Icon: FaAws, color: '#FF9900' },
  aws_rds_postgres: { Icon: FaAws, color: '#FF9900' },
  aws_rds_mysql: { Icon: FaAws, color: '#FF9900' },
  aws_stepfunctions: { Icon: FaAws, color: '#FF9900' },
  aws_sns_sqs: { Icon: FaAws, color: '#FF9900' },
  aws_pricing: { Icon: FaAws, color: '#FF9900' },
  aws_documentdb: { Icon: FaAws, color: '#FF9900' },
  aws_elasticache: { Icon: FaAws, color: '#FF9900' },
  aws_cloudtrail: { Icon: FaAws, color: '#FF9900' },
  aws_bedrock_kb: { Icon: FaAws, color: '#FF9900' },

  // Microsoft doesn't license the Azure mark for open reproduction (it's
  // absent from Simple Icons) — an honest neutral cloud glyph in Azure blue
  // instead of a fabricated logo.
  azure: { Icon: Cloud, color: '#0078D4' },

  // Google Cloud has a real Simple Icons mark.
  gcp_storage: { Icon: SiGooglecloud, color: '#4285F4' },
  gcp_platform: { Icon: SiGooglecloud, color: '#4285F4' },

  // The bundled built-in rows (app.core.workflows.seed) are really just a
  // SQLite database and a Qdrant store under the hood — same icon as the
  // real engine, not a generic fallback, since they're not a different
  // product from what the Marketplace catalog itself offers.
  aegis_app_db: { Icon: SiSqlite, color: '#003B57' },
  aegis_hybrid: { Icon: SiQdrant, color: '#DC244C' },
  lancedb: { Icon: Layers, color: '#FF6A3D' },   // no published brand mark yet
  chromadb: { Icon: Database, color: '#8B5CF6' }, // no published brand mark yet

  // Marketplace — automation tools, extraction engines, and downloadable models
  media_transcription: { Icon: AudioLines, color: '#F4622D' },
  ocr_extraction: { Icon: ScanText, color: '#8B5CF6' },
  document_extraction: { Icon: FileText, color: '#8A94A6' },
  // Per-format Document Extraction cards (keyed by format, not by the
  // specific engine — e.g. every PDF engine, pymupdf/pdfplumber/pypdf/...,
  // shares the same icon, since the icon represents the FILE FORMAT, not
  // which library reads it; the card's own name/description already says
  // which engine it is). See MarketplaceView.tsx's ToolCard.
  extract_pdf: { Icon: FileText, color: '#E23F3F' },
  extract_docx: { Icon: FileType2, color: '#2B579A' },
  extract_pptx: { Icon: Presentation, color: '#D24726' },
  extract_xlsx: { Icon: FileSpreadsheet, color: '#217346' },
  extract_text: { Icon: FileText, color: '#6B7280' },
  embedding: { Icon: SiHuggingface, color: '#FFD21E' }, // fastembed's catalog is mostly HF-hub-hosted models
  reranker: { Icon: ArrowDownUp, color: '#F4622D' },
};

const DEFAULT_ICON: ServiceIconSpec = { Icon: Wrench, color: '#8A94A6' };

export function getServiceIcon(key?: string | null): ServiceIconSpec {
  if (!key) return DEFAULT_ICON;
  return SERVICE_ICONS[key] || DEFAULT_ICON;
}
