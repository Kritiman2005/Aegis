'use client';

import { useState } from 'react';
import { X, Key, ExternalLink, Loader2 } from 'lucide-react';

export interface CatalogConnector {
  name: string;
  display_name: string;
  category: string;
  description: string;
  icon: string;
  auth_type: 'oauth' | 'api_key' | 'connection_string' | 'path' | 'none';
  oauth_service?: string;
  env_schema?: Array<{
    key: string;
    label: string;
    placeholder?: string;
    required?: boolean;
    secret?: boolean;
    help_url?: string;
  }>;
  input_schema?: Array<{
    key: string;
    label: string;
    placeholder?: string;
    required?: boolean;
    secret?: boolean;
  }>;
  target_audience?: string[];
  official?: boolean;
}

interface ConfigModalProps {
  connector: CatalogConnector | null;
  isOpen: boolean;
  onClose: () => void;
  onConnect: (connectorName: string, env: Record<string, string>, inputParams: Record<string, string>) => Promise<void>;
}

export default function ConfigModal({
  connector,
  isOpen,
  onClose,
  onConnect,
}: ConfigModalProps) {
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen || !connector) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await onConnect(connector.name, envValues, inputValues);
      onClose();
    } catch (err: any) {
      setError(err?.message || 'Connection failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 animate-fade-in">
      <div className="bg-aegis-raised rounded-2xl border border-aegis-border shadow-2xl max-w-lg w-full overflow-hidden">
        {/* Header */}
        <div className="p-6 border-b border-aegis-border flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-aegis-overlay border border-aegis-border flex items-center justify-center text-aegis-text-primary text-lg font-bold">
              {connector.display_name.charAt(0)}
            </div>
            <div>
              <h3 className="text-base font-bold text-aegis-text-primary">{connector.display_name}</h3>
              <p className="text-xs text-aegis-text-secondary">{connector.category}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-aegis-text-muted hover:text-aegis-text-primary hover:bg-aegis-overlay transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <p className="text-xs text-aegis-text-secondary leading-relaxed">
            {connector.description}
          </p>

          {/* Environment Schema Fields (API Keys / Tokens) */}
          {connector.env_schema?.map((field) => (
            <div key={field.key} className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-aegis-text-secondary flex items-center gap-1.5">
                  <Key className="w-3.5 h-3.5 text-aegis-text-muted" />
                  {field.label} {field.required && <span className="text-aegis-error">*</span>}
                </label>
                {field.help_url && (
                  <a
                    href={field.help_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[10px] text-aegis-primary-light hover:underline flex items-center gap-1"
                  >
                    Get Token <ExternalLink className="w-3 h-3" />
                  </a>
                )}
              </div>
              <input
                type={field.secret ? 'password' : 'text'}
                placeholder={field.placeholder || `Enter ${field.label}`}
                value={envValues[field.key] || ''}
                onChange={(e) => setEnvValues({ ...envValues, [field.key]: e.target.value })}
                required={field.required}
                className="w-full bg-aegis-overlay border border-aegis-border rounded-xl px-3.5 py-2 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:border-aegis-primary transition-all font-mono"
              />
            </div>
          ))}

          {/* Input Schema Fields (Domain / Path arguments) */}
          {connector.input_schema?.map((field) => (
            <div key={field.key} className="space-y-1">
              <label className="text-xs font-semibold text-aegis-text-secondary">
                {field.label} {field.required && <span className="text-aegis-error">*</span>}
              </label>
              <input
                type="text"
                placeholder={field.placeholder || `Enter ${field.label}`}
                value={inputValues[field.key] || ''}
                onChange={(e) => setInputValues({ ...inputValues, [field.key]: e.target.value })}
                required={field.required}
                className="w-full bg-aegis-overlay border border-aegis-border rounded-xl px-3.5 py-2 text-xs text-aegis-text-primary placeholder:text-aegis-text-muted focus:outline-none focus:border-aegis-primary transition-all"
              />
            </div>
          ))}

          {error && (
            <div className="p-3 rounded-xl bg-aegis-error/10 border border-aegis-error/30 text-xs text-aegis-error">
              {error}
            </div>
          )}

          {/* Footer Actions */}
          <div className="pt-4 flex items-center justify-end gap-3 border-t border-aegis-border">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-xs font-medium text-aegis-text-secondary hover:bg-aegis-overlay transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-5 py-2 rounded-xl bg-aegis-primary text-white text-xs font-semibold hover:bg-aegis-primary-dark transition-all flex items-center gap-2 disabled:opacity-50"
            >
              {loading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              Connect {connector.display_name}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
