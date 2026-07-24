import { useState, useEffect } from 'react';
import { X, Trash2, Edit2, Loader2, Save } from 'lucide-react';

interface Memory {
  id: number;
  conversation_id: string;
  label: string;
  entity_type: string;
  entity_id: string;
  data_json: string;
  created_at: string;
}

export default function MemoryViewer({ onClose }: { onClose: () => void }) {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editLabel, setEditLabel] = useState('');
  const [editData, setEditData] = useState('');

  const fetchMemories = async () => {
    try {
      setLoading(true);
      const res = await fetch('http://127.0.0.1:8000/api/memories');
      const data = await res.json();
      setMemories(data);
    } catch (err) {
      console.error('Failed to fetch memories', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMemories();
  }, []);

  const handleDelete = async (id: number) => {
    try {
      await fetch(`http://127.0.0.1:8000/api/memories/${id}`, { method: 'DELETE' });
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch (err) {
      console.error('Failed to delete memory', err);
    }
  };

  const startEdit = (m: Memory) => {
    setEditingId(m.id);
    setEditLabel(m.label);
    setEditData(m.data_json);
  };

  const saveEdit = async (id: number) => {
    try {
      await fetch(`http://127.0.0.1:8000/api/memories/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: editLabel, data_json: editData }),
      });
      setMemories((prev) =>
        prev.map((m) => (m.id === id ? { ...m, label: editLabel, data_json: editData } : m))
      );
      setEditingId(null);
    } catch (err) {
      console.error('Failed to update memory', err);
    }
  };

  return (
    <div className="fixed inset-y-0 right-0 w-96 bg-white shadow-2xl border-l border-gray-200 z-50 flex flex-col transform transition-transform duration-300">
      <div className="h-16 px-6 border-b border-gray-100 flex items-center justify-between flex-shrink-0 bg-gray-50/50">
        <h2 className="text-sm font-bold text-gray-900 tracking-tight flex items-center gap-2">
          Global Memory Store
        </h2>
        <button onClick={onClose} className="p-2 hover:bg-gray-200 rounded-full text-gray-500 transition-colors">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-[#F8F9FA]">
        {loading ? (
          <div className="flex justify-center py-10">
            <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
          </div>
        ) : memories.length === 0 ? (
          <div className="text-center py-10 text-xs text-gray-400">
            No memories saved yet.
          </div>
        ) : (
          memories.map((m) => (
            <div key={m.id} className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex flex-col gap-2 relative group">
              {editingId === m.id ? (
                <div className="space-y-2">
                  <input
                    value={editLabel}
                    onChange={(e) => setEditLabel(e.target.value)}
                    className="w-full text-sm font-semibold border-b border-gray-300 focus:border-indigo-500 outline-none pb-1"
                  />
                  <textarea
                    value={editData}
                    onChange={(e) => setEditData(e.target.value)}
                    className="w-full text-xs font-mono bg-gray-50 border border-gray-200 rounded p-2 h-24 outline-none focus:border-indigo-500"
                  />
                  <div className="flex justify-end gap-2">
                    <button onClick={() => setEditingId(null)} className="text-xs text-gray-500 hover:text-gray-900">Cancel</button>
                    <button onClick={() => saveEdit(m.id)} className="text-xs flex items-center gap-1 bg-indigo-600 text-white px-2 py-1 rounded hover:bg-indigo-700">
                      <Save className="w-3 h-3" /> Save
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="text-sm font-semibold text-gray-900">{m.label}</h3>
                      <span className="text-[10px] uppercase font-bold text-gray-400 tracking-wider">
                        {m.entity_type.replace('_', ' ')}
                      </span>
                    </div>
                    <div className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1">
                      <button onClick={() => startEdit(m)} className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded">
                        <Edit2 className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handleDelete(m.id)} className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                  <pre className="text-[10px] text-gray-600 bg-gray-50 p-2 rounded border border-gray-100 overflow-x-auto whitespace-pre-wrap max-h-32">
                    {m.data_json}
                  </pre>
                  <div className="text-[10px] text-gray-400 flex justify-between mt-1">
                    <span>{new Date(m.created_at).toLocaleDateString()}</span>
                    <span className="truncate max-w-[120px]" title={m.conversation_id}>
                      Thread: {m.conversation_id.slice(0, 8)}...
                    </span>
                  </div>
                </>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
