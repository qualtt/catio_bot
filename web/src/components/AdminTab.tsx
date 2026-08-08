import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { ShieldCheck, Check, X, RefreshCw } from 'lucide-react';

interface AdminTabProps {
  apiBase: string;
  token: string;
}

interface PendingPost {
  id: number;
  photo_id: number | null;
  image_url: string | null;
  user_id: number;
  user_name: string | null;
  animal_type: string | null;
  created_at: string;
}

export const AdminTab: React.FC<AdminTabProps> = ({ apiBase, token }) => {
  const [pendingPosts, setPendingPosts] = useState<PendingPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<number | null>(null);

  const fetchPending = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${apiBase}/admin/pending`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setPendingPosts(res.data);
    } catch (err) {
      console.error("Failed to load admin queue", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPending();
  }, [apiBase, token]);

  const handleApprove = async (postId: number) => {
    setActionId(postId);
    try {
      await axios.post(
        `${apiBase}/admin/posts/${postId}/approve`,
        {},
        { headers: { Authorization: `Bearer ${token}` } }
      );
      setPendingPosts((prev) => prev.filter((p) => p.id !== postId));
    } catch (err) {
      console.error("Approve failed", err);
    } finally {
      setActionId(null);
    }
  };

  const handleReject = async (postId: number) => {
    setActionId(postId);
    try {
      await axios.post(
        `${apiBase}/admin/posts/${postId}/reject`,
        { reason: 'Отклонено модератором' },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      setPendingPosts((prev) => prev.filter((p) => p.id !== postId));
    } catch (err) {
      console.error("Reject failed", err);
    } finally {
      setActionId(null);
    }
  };

  return (
    <div className="animate-fade-in" style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
            <ShieldCheck color="#34d399" size={20} /> Панель Модератора
          </h2>
          <p style={{ fontSize: 12, color: 'var(--hint-color)' }}>В очереди на проверку: {pendingPosts.length}</p>
        </div>
        <button className="btn-secondary" onClick={fetchPending} style={{ padding: '6px 12px', fontSize: 12 }}>
          <RefreshCw size={14} /> Обновить
        </button>
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--hint-color)' }}>Загрузка очереди...</div>
      ) : pendingPosts.length === 0 ? (
        <div className="glass-panel" style={{ padding: 30, textAlign: 'center' }}>
          <Check color="#34d399" size={40} style={{ marginBottom: 8 }} />
          <h3>Очередь чиста!</h3>
          <p style={{ fontSize: 13, color: 'var(--hint-color)', marginTop: 4 }}>Нет предложенных фото, ожидающих проверки.</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {pendingPosts.map((post) => (
            <div key={post.id} className="glass-panel" style={{ overflow: 'hidden', borderRadius: 16 }}>
              {post.image_url && (
                <img
                  src={`${apiBase.replace('/api/v1', '')}${post.image_url}`}
                  alt="Кандидат"
                  style={{ width: '100%', height: 220, objectFit: 'cover' }}
                />
              )}
              <div style={{ padding: 14 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
                  <div>
                    <span style={{ fontSize: 12, color: 'var(--hint-color)' }}>Автор: {post.user_name}</span>
                    <h4 style={{ fontSize: 15, fontWeight: 700 }}>{post.animal_type || 'Тип не указан'}</h4>
                  </div>
                  <span className="badge badge-purple">#Post {post.id}</span>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  <button
                    onClick={() => handleApprove(post.id)}
                    disabled={actionId === post.id}
                    style={{
                      background: '#10b981',
                      color: 'white',
                      border: 'none',
                      padding: 10,
                      borderRadius: 12,
                      fontSize: 13,
                      fontWeight: 700,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: 4,
                    }}
                  >
                    <Check size={16} /> Одобрить
                  </button>
                  <button
                    onClick={() => handleReject(post.id)}
                    disabled={actionId === post.id}
                    style={{
                      background: '#ef4444',
                      color: 'white',
                      border: 'none',
                      padding: 10,
                      borderRadius: 12,
                      fontSize: 13,
                      fontWeight: 700,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: 4,
                    }}
                  >
                    <X size={16} /> Отклонить
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
