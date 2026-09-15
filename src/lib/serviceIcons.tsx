import type { IconType } from 'react-icons';
import {
  SiGmail, SiGoogledrive, SiGooglesheets, SiGoogledocs, SiNotion, SiFigma, SiHubspot, SiAirtable,
  SiLinear, SiJira, SiStripe, SiShopify, SiBrave, SiGooglemaps, SiZendesk,
  SiGithub, SiPostgresql, SiSentry, SiElasticsearch, SiSqlite, SiDuckdb,
  SiQdrant, SiGit, SiHuggingface,
} from 'react-icons/si';
import { FaSlack, FaSalesforce } from 'react-icons/fa6';
import {
  Folder, Globe, Brain, Cpu, Clock, Database, Layers, Wrench,
  FileText, FileType2, Presentation, FileSpreadsheet, AudioLines, ScanText,
  ArrowDownUp,
  type LucideIcon,
} from 'lucide-react';

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
  // The bundled built-in rows (app.core.workflows.seed) are really just a
  // SQLite database and a Qdrant store under the hood — same icon as the
  // real engine, not a generic fallback, since they're not a different
  // product from what the Marketplace catalog itself offers.
  aegis_app_db: { Icon: SiSqlite, color: '#003B57' },
  aegis_hybrid: { Icon: SiQdrant, color: '#DC244C' },
  lancedb: { Icon: Layers, color: '#FF6A3D' },   // no published brand mark yet
  chromadb: { Icon: Database, color: '#8B5CF6' }, // no published brand mark yet

  // Marketplace — automation tools, extraction engines, and downloadable models
  playwright_scraper: { Icon: Globe, color: '#2EAD33' }, // no published Playwright brand mark in this icon set
  web_extraction: { Icon: Globe, color: '#4B9EF4' },
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

// Rounded logo tile — neutral background so brand colors (including
// low-contrast ones like DuckDB's yellow) always stay readable in both
// themes, with a faint colored ring as the brand hint.
export function ServiceLogo({ serviceKey, size = 'md' }: { serviceKey?: string | null; size?: 'sm' | 'md' }) {
  const { Icon, color } = getServiceIcon(serviceKey);
  const box = size === 'sm' ? 'w-8 h-8 rounded-lg' : 'w-10 h-10 rounded-xl';
  const iconSize = size === 'sm' ? 16 : 20;
  return (
    <div
      className={`${box} bg-aegis-overlay flex items-center justify-center flex-shrink-0 ring-1`}
      style={{ ['--tw-ring-color' as any]: `${color}33` }}
    >
      <Icon size={iconSize} color={color} />
    </div>
  );
}
