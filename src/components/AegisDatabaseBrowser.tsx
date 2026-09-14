import React, { useState, useEffect, useCallback } from 'react';
import { X, Plus, Trash2, Table2, Loader2, ChevronLeft, ChevronRight, ShieldCheck } from 'lucide-react';
import toast from 'react-hot-toast';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
const PAGE_SIZE = 50;

// Mirrors backend/app/core/aegis_db_browser.py's list_tables() output —
// every table here already passed that module's safety allowlist; this
// component never decides what's safe, it only renders what the backend
// already filtered.
interface DbColumn {
  name: string;
  type: string;
}

interface DbTable {
  name: string;
  label: string;
  columns: DbColumn[];
  row_count: number;
  user_created: boolean;
}

const inputClass = "w-full bg-aegis-overlay border border-aegis-border rounded-md px-2 py-1 text-xs text-aegis-text-primary focus:outline-none focus:ring-1 focus:ring-aegis-primary";

function inputTypeFor(colType: string): string {
  const t = colType.toUpperCase();
  if (t.includes('INT')) return 'number';
  if (t.includes('REAL') || t.includes('FLOA') || t.includes('DOUB')) return 'number';
  return 'text';
}

export default function AegisDatabaseBrowser({ onClose }: { onClose: () => void }) {
  const [tables, setTables] = useState<DbTable[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, any>[]>([]);
  const [rowCount, setRowCount] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newTableName, setNewTableName] = useState('');
  const [newColumns, setNewColumns] = useState<{ name: string; type: string; nullable: boolean }[]>([
    { name: '', type: 'text', nullable: true },
  ]);

  const fetchTables = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/aegis-db/tables`);
      if (res.ok) setTables((await res.json()).tables || []);
    } catch (e) {}
  }, []);

  const fetchRows = useCallback(async (table: string, pageNum: number) => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/aegis-db/tables/${table}/rows?limit=${PAGE_SIZE}&offset=${pageNum * PAGE_SIZE}`);
      if (res.ok) {
        const data = await res.json();
        setRows(data.rows || []);
        setRowCount(data.row_count || 0);
      }
    } catch (e) {
      toast.error('Could not reach the backend.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchTables(); }, [fetchTables]);
  useEffect(() => {
    if (selected) { setPage(0); fetchRows(selected, 0); }
  }, [selected, fetchRows]);

  const selectedTable = tables.find(t => t.name === selected);

  const changePage = (delta: number) => {
    if (!selected) return;
    const next = Math.max(0, page + delta);
    setPage(next);
    fetchRows(selected, next);
  };

  const updateCell = async (row: Record<string, any>, col: string, value: string) => {
    if (!selected) return;
    try {
      const res = await fetch(`${API_BASE}/api/aegis-db/tables/${selected}/rows/${row.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes: { [col]: value } }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || 'Update failed.');
        return;
      }
      setRows(prev => prev.map(r => (r.id === row.id ? { ...r, [col]: value } : r)));
    } catch (e) {
      toast.error('Could not reach the backend.');
    }
  };

  const addRow = async () => {
    if (!selected || !selectedTable) return;
    const values: Record<string, string> = {};
    selectedTable.columns.forEach(c => { if (c.name !== 'id') values[c.name] = ''; });
    try {
      const res = await fetch(`${API_BASE}/api/aegis-db/tables/${selected}/rows`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || 'Insert failed.');
        return;
      }
      fetchRows(selected, page);
      fetchTables();
    } catch (e) {
      toast.error('Could not reach the backend.');
    }
  };

  const deleteRow = async (row: Record<string, any>) => {
    if (!selected) return;
    try {
      const res = await fetch(`${API_BASE}/api/aegis-db/tables/${selected}/rows/${row.id}`, { method: 'DELETE' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || 'Delete failed.');
        return;
      }
      fetchRows(selected, page);
      fetchTables();
    } catch (e) {
      toast.error('Could not reach the backend.');
    }
  };

  const addColumnRow = () => setNewColumns(prev => [...prev, { name: '', type: 'text', nullable: true }]);
  const removeColumnRow = (i: number) => setNewColumns(prev => prev.filter((_, idx) => idx !== i));

  const submitNewTable = async () => {
    const name = newTableName.trim();
    if (!name) { toast.error('Name the table.'); return; }
    const columns = newColumns.filter(c => c.name.trim());
    if (columns.length === 0) { toast.error('Add at least one column.'); return; }
    try {
      const res = await fetch(`${API_BASE}/api/aegis-db/tables`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, columns }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || 'Could not create the table.');
        return;
      }
      toast.success('Table created.');
      setCreating(false);
      setNewTableName('');
      setNewColumns([{ name: '', type: 'text', nullable: true }]);
      await fetchTables();
      setSelected(name);
    } catch (e) {
      toast.error('Could not reach the backend.');
    }
  };

  const dropTable = async (name: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/aegis-db/tables/${name}`, { method: 'DELETE' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || 'Could not drop the table.');
        return;
      }
      toast.success(`Dropped '${name}'.`);
      if (selected === name) setSelected(null);
      fetchTables();
    } catch (e) {
      toast.error('Could not reach the backend.');
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 animate-fade-in" onClick={onClose}>
      <div
        className="bg-aegis-raised rounded-2xl border border-aegis-border shadow-2xl max-w-5xl w-full h-[80vh] overflow-hidden flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-aegis-border flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-2.5">
            <Table2 className="w-5 h-5 text-aegis-primary" />
            <div>
              <h3 className="text-sm font-bold text-aegis-text-primary">SQLite</h3>
              <p className="text-[11px] text-aegis-text-muted flex items-center gap-1">
                <ShieldCheck className="w-3 h-3" /> Only a safe, credential-free set of tables is ever shown here
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-aegis-text-muted hover:text-aegis-text-primary hover:bg-aegis-overlay transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 flex min-h-0">
          {/* Table sidebar */}
          <div className="w-56 border-r border-aegis-border flex flex-col flex-shrink-0">
            <div className="flex-1 overflow-y-auto p-2">
              {tables.map(t => (
                <div key={t.name} className="group flex items-center">
                  <button
                    onClick={() => { setSelected(t.name); setCreating(false); }}
                    className={`flex-1 min-w-0 text-left px-2.5 py-2 rounded-md text-xs transition-colors ${selected === t.name && !creating ? 'bg-aegis-primary text-white' : 'text-aegis-text-secondary hover:bg-aegis-overlay hover:text-aegis-text-primary'}`}
                  >
                    <div className="font-semibold truncate">{t.label}</div>
                    <div className={`text-[10px] ${selected === t.name && !creating ? 'text-white/70' : 'text-aegis-text-muted'}`}>{t.row_count} row{t.row_count === 1 ? '' : 's'}</div>
                  </button>
                  {t.user_created && (
                    <button
                      onClick={() => dropTable(t.name)}
                      title="Drop table"
                      className="p-1.5 rounded-md opacity-0 group-hover:opacity-100 hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error transition-opacity flex-shrink-0"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  )}
                </div>
              ))}
            </div>
            <div className="p-2 border-t border-aegis-border flex-shrink-0">
              <button
                onClick={() => { setCreating(true); setSelected(null); }}
                className={`w-full flex items-center justify-center gap-1.5 px-2.5 py-2 rounded-md text-xs font-semibold transition-colors ${creating ? 'bg-aegis-primary text-white' : 'bg-aegis-overlay text-aegis-text-primary hover:bg-aegis-base'}`}
              >
                <Plus className="w-3.5 h-3.5" /> New table
              </button>
            </div>
          </div>

          {/* Main area */}
          <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
            {creating ? (
              <div className="p-5 overflow-y-auto space-y-3">
                <div>
                  <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Table name</label>
                  <input
                    value={newTableName}
                    onChange={e => setNewTableName(e.target.value)}
                    placeholder="my_table"
                    className={`${inputClass} mt-1`}
                  />
                </div>
                <div>
                  <label className="text-[10px] font-semibold text-aegis-text-muted uppercase">Columns</label>
                  <p className="text-[10px] text-aegis-text-muted mb-2">An "id" primary key column is added automatically.</p>
                  <div className="flex flex-col gap-1.5">
                    {newColumns.map((col, i) => (
                      <div key={i} className="flex items-center gap-1.5">
                        <input
                          value={col.name}
                          onChange={e => setNewColumns(prev => prev.map((c, idx) => idx === i ? { ...c, name: e.target.value } : c))}
                          placeholder="column_name"
                          className={`${inputClass} flex-1`}
                        />
                        <select
                          value={col.type}
                          onChange={e => setNewColumns(prev => prev.map((c, idx) => idx === i ? { ...c, type: e.target.value } : c))}
                          className={`${inputClass} w-28`}
                        >
                          <option value="text">Text</option>
                          <option value="integer">Integer</option>
                          <option value="real">Real</option>
                          <option value="boolean">Boolean</option>
                        </select>
                        <label className="flex items-center gap-1 text-[10px] text-aegis-text-muted flex-shrink-0">
                          <input
                            type="checkbox"
                            checked={col.nullable}
                            onChange={e => setNewColumns(prev => prev.map((c, idx) => idx === i ? { ...c, nullable: e.target.checked } : c))}
                          />
                          Nullable
                        </label>
                        <button onClick={() => removeColumnRow(i)} className="p-1 rounded hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error flex-shrink-0">
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                  <button onClick={addColumnRow} className="mt-2 flex items-center gap-1.5 text-[11px] font-semibold text-aegis-primary-light hover:underline">
                    <Plus className="w-3 h-3" /> Add column
                  </button>
                </div>
                <div className="flex justify-end pt-2">
                  <button
                    onClick={submitNewTable}
                    className="flex items-center gap-1.5 px-3.5 py-1.5 bg-aegis-primary text-white text-xs font-semibold rounded-lg hover:opacity-90 transition-opacity"
                  >
                    <Plus className="w-3.5 h-3.5" /> Create table
                  </button>
                </div>
              </div>
            ) : !selectedTable ? (
              <div className="flex-1 flex items-center justify-center text-xs text-aegis-text-muted">
                Select a table, or create a new one.
              </div>
            ) : (
              <>
                <div className="flex-1 overflow-auto">
                  {loading ? (
                    <div className="p-5 text-xs text-aegis-text-muted flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…</div>
                  ) : (
                    <table className="w-full text-xs border-collapse">
                      <thead className="sticky top-0 bg-aegis-raised">
                        <tr>
                          {selectedTable.columns.map(c => (
                            <th key={c.name} className="text-left font-semibold text-aegis-text-muted uppercase text-[10px] px-3 py-2 border-b border-aegis-border whitespace-nowrap">{c.name}</th>
                          ))}
                          {selectedTable.user_created && <th className="border-b border-aegis-border w-8" />}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row, ri) => (
                          <tr key={row.id ?? ri} className="hover:bg-aegis-overlay">
                            {selectedTable.columns.map(c => (
                              <td key={c.name} className="px-1.5 py-1 border-b border-aegis-border">
                                {c.name === 'id' || !selectedTable.user_created ? (
                                  <span className="px-1.5 text-aegis-text-muted">{String(row[c.name] ?? '')}</span>
                                ) : (
                                  <input
                                    defaultValue={row[c.name] ?? ''}
                                    type={inputTypeFor(c.type)}
                                    onBlur={e => { if (e.target.value !== String(row[c.name] ?? '')) updateCell(row, c.name, e.target.value); }}
                                    className="w-full min-w-[80px] bg-transparent px-1.5 py-1 text-xs text-aegis-text-primary rounded focus:outline-none focus:ring-1 focus:ring-aegis-primary focus:bg-aegis-overlay"
                                  />
                                )}
                              </td>
                            ))}
                            {selectedTable.user_created && (
                              <td className="px-1.5 py-1 border-b border-aegis-border">
                                <button onClick={() => deleteRow(row)} className="p-1 rounded hover:bg-aegis-error/10 text-aegis-text-muted hover:text-aegis-error">
                                  <Trash2 className="w-3 h-3" />
                                </button>
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
                <div className="flex items-center justify-between px-3 py-2.5 border-t border-aegis-border flex-shrink-0">
                  {selectedTable.user_created ? (
                    <button
                      onClick={addRow}
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-semibold text-aegis-primary-light hover:bg-aegis-overlay transition-colors"
                    >
                      <Plus className="w-3.5 h-3.5" /> Add row
                    </button>
                  ) : (
                    <span className="flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] text-aegis-text-muted">
                      <ShieldCheck className="w-3.5 h-3.5" /> System table — read-only. Create your own table to edit data.
                    </span>
                  )}
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-aegis-text-muted">
                      {rowCount === 0 ? '0 rows' : `${page * PAGE_SIZE + 1}–${Math.min(rowCount, (page + 1) * PAGE_SIZE)} of ${rowCount}`}
                    </span>
                    <button onClick={() => changePage(-1)} disabled={page === 0} className="p-1 rounded hover:bg-aegis-overlay text-aegis-text-secondary disabled:opacity-30">
                      <ChevronLeft className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => changePage(1)} disabled={(page + 1) * PAGE_SIZE >= rowCount} className="p-1 rounded hover:bg-aegis-overlay text-aegis-text-secondary disabled:opacity-30">
                      <ChevronRight className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
