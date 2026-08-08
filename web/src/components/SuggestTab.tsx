import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { UploadCloud, Check, Sparkles, CheckCircle } from 'lucide-react';

interface SuggestTabProps {
  apiBase: string;
  token: string;
}

interface AnimalType {
  id: number;
  name: string;
  is_primary: boolean;
}

export const SuggestTab: React.FC<SuggestTabProps> = ({ apiBase, token }) => {
  const [animalTypes, setAnimalTypes] = useState<AnimalType[]>([]);
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    axios
      .get(`${apiBase}/photos/animal-types`)
      .then((res) => setAnimalTypes(res.data))
      .catch((err) => console.error("Failed to load animal types", err));
  }, [apiBase]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setSelectedFile(file);
      setPreviewUrl(URL.createObjectURL(file));
      setSuccessMsg(null);
    }
  };

  const handleUpload = async () => {
    if (!selectedFile) return;
    setUploading(true);

    if (window.Telegram?.WebApp?.HapticFeedback) {
      window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
    }

    const formData = new FormData();
    formData.append('file', selectedFile);
    if (selectedType) {
      formData.append('animal_type', selectedType);
    }

    try {
      const res = await axios.post(`${apiBase}/photos/upload`, formData, {
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'multipart/form-data',
        },
      });

      setSuccessMsg(`Фото ушло на модерацию! (ID: ${res.data.photo_id})`);
      setSelectedFile(null);
      setPreviewUrl(null);
      setSelectedType(null);
    } catch (err) {
      console.error("Upload failed", err);
      alert("Ошибка при загрузке фото. Попробуйте еще раз.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="animate-fade-in" style={{ padding: 16 }}>
      <div style={{ textAlign: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 800 }}>Предложить фото котика или животного 🐾</h2>
        <p style={{ fontSize: 13, color: 'var(--hint-color)', marginTop: 4 }}>
          Ваше фото попадет в модерацию и канал бота после одобрения!
        </p>
      </div>

      {successMsg && (
        <div className="glass-panel" style={{ padding: 16, marginBottom: 16, borderLeft: '4px solid #34d399', display: 'flex', alignItems: 'center', gap: 12 }}>
          <CheckCircle color="#34d399" size={24} />
          <span style={{ fontSize: 14, fontWeight: 600 }}>{successMsg}</span>
        </div>
      )}

      {/* Upload Zone */}
      <div
        className="glass-panel"
        style={{
          padding: 24,
          textAlign: 'center',
          border: '2px dashed var(--glass-border)',
          borderRadius: 20,
          marginBottom: 16,
          cursor: 'pointer',
          position: 'relative',
        }}
      >
        <input
          type="file"
          accept="image/*"
          onChange={handleFileChange}
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            opacity: 0,
            cursor: 'pointer',
          }}
        />

        {previewUrl ? (
          <div>
            <img
              src={previewUrl}
              alt="Превью"
              style={{ width: '100%', maxHeight: '200px', objectFit: 'cover', borderRadius: 12, marginBottom: 12 }}
            />
            <span style={{ fontSize: 13, color: '#38bdf8', fontWeight: 600 }}>Нажмите, чтобы сменить фото</span>
          </div>
        ) : (
          <div>
            <UploadCloud size={40} color="#a855f7" style={{ marginBottom: 8 }} />
            <h4 style={{ fontSize: 15, fontWeight: 700 }}>Выберите или перетащите фото</h4>
            <p style={{ fontSize: 12, color: 'var(--hint-color)', marginTop: 4 }}>Поддерживаются JPG, PNG, WebP</p>
          </div>
        )}
      </div>

      {/* Animal Type Selection */}
      <div style={{ marginBottom: 16 }}>
        <h4 style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Sparkles size={16} color="#fbbf24" /> Выберите категорию животного
        </h4>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {animalTypes.map((type) => {
            const isSelected = selectedType === type.name;
            return (
              <button
                key={type.id}
                onClick={() => setSelectedType(isSelected ? null : type.name)}
                style={{
                  background: isSelected ? 'var(--accent-gradient)' : 'rgba(30, 41, 59, 0.7)',
                  color: isSelected ? '#ffffff' : 'var(--text-color)',
                  border: isSelected ? 'none' : '1px solid var(--glass-border)',
                  padding: '8px 14px',
                  borderRadius: 12,
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                }}
              >
                {isSelected && <Check size={14} />}
                {type.name}
              </button>
            );
          })}
        </div>
      </div>

      {/* Submit Button */}
      <button
        className="btn-primary"
        onClick={handleUpload}
        disabled={!selectedFile || uploading}
        style={{
          width: '100%',
          opacity: !selectedFile || uploading ? 0.5 : 1,
        }}
      >
        {uploading ? 'Отправка...' : 'Отправить в бот ✨'}
      </button>
    </div>
  );
};
