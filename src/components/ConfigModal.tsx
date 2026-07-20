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
      <div className="bg-white rounded-2xl border border-gray-200 shadow-2xl max-w-lg w-full overflow-hidden">
        {/* Header */}
        <div className="p-6 border-b border-gray-100 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-800 text-lg font-bold">
              {connector.display_name.charAt(0)}
            </div>
            <div>
              <h3 className="text-base font-bold text-gray-900">{connector.display_name}</h3>
              <p className="text-xs text-gray-500">{connector.category}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <p className="text-xs text-gray-600 leading-relaxed">
            {connector.description}
          </p>

          {/* Environment Schema Fields (API Keys / Tokens) */}
          {connector.env_schema?.map((field) => (
            <div key={field.key} className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-gray-700 flex items-center gap-1.5">
                  <Key className="w-3.5 h-3.5 text-gray-400" />
                  {field.label} {field.required && <span className="text-red-500">*</span>}
                </label>
                {field.help_url && (
                  <a
                    href={field.help_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[10px] text-indigo-600 hover:underline flex items-center gap-1"
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
                className="w-full bg-gray-50 border border-gray-200 rounded-xl px-3.5 py-2 text-xs text-gray-900 placeholder:text-gray-400 focus:outline-none focus:border-black focus:bg-white transition-all font-mono"
              />
            </div>
          ))}

          {/* Input Schema Fields (Domain / Path arguments) */}
          {connector.input_schema?.map((field) => (
            <div key={field.key} className="space-y-1">
              <label className="text-xs font-semibold text-gray-700">
                {field.label} {field.required && <span className="text-red-500">*</span>}
              </label>
              <input
                type="text"
                placeholder={field.placeholder || `Enter ${field.label}`}
                value={inputValues[field.key] || ''}
                onChange={(e) => setInputValues({ ...inputValues, [field.key]: e.target.value })}
                required={field.required}
                className="w-full bg-gray-50 border border-gray-200 rounded-xl px-3.5 py-2 text-xs text-gray-900 placeholder:text-gray-400 focus:outline-none focus:border-black focus:bg-white transition-all"
              />
            </div>
          ))}

          {error && (
            <div className="p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-600">
              {error}
            </div>
          )}

          {/* Footer Actions */}
          <div className="pt-4 flex items-center justify-end gap-3 border-t border-gray-100">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-xs font-medium text-gray-600 hover:bg-gray-100 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-5 py-2 rounded-xl bg-black text-white text-xs font-semibold hover:bg-neutral-800 transition-all flex items-center gap-2 shadow-sm disabled:opacity-50"
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
