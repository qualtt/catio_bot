import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { UploadCloud, Check, Sparkles, CheckCircle, AlertTriangle, Calendar, Clock } from 'lucide-react';

interface SuggestTabProps {
  apiBase: string;
  token: string;
}

interface AnimalType {
  id: number;
  name: string;
  is_primary: boolean;
}

interface UploadResult {
  photo_id: number;
  post_id: number;
  animal_type: string;
  ai_comment: string | null;
  duplicate_of_photo_id: number | null;
  duplicate_distance: number | null;
}

export const SuggestTab: React.FC<SuggestTabProps> = ({ apiBase, token }) => {
  const [animalTypes, setAnimalTypes] = useState<AnimalType[]>([]);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const [step, setStep] = useState<'upload' | 'customize' | 'success'>('upload');
  const [uploading, setUploading] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const [uploadData, setUploadData] = useState<UploadResult | null>(null);
  const [selectedType, setSelectedType] = useState<string>('Кот');
  const [isAutoScheduled, setIsAutoScheduled] = useState(true);
  const [customScheduleTime, setCustomScheduleTime] = useState('');

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
      setStep('upload');
    }
  };

  const handleUpload = async () => {
    if (!selectedFile) return;
    setUploading(true);

    if (window.Telegram?.WebApp?.HapticFeedback) {
      window.Telegram.WebApp.HapticFeedback.impactOccurred('medium');
    }

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
      const res = await axios.post<UploadResult>(`${apiBase}/photos/upload`, formData, {
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'multipart/form-data',
        },
      });

      setUploadData(res.data);
      if (res.data.animal_type) {
        setSelectedType(res.data.animal_type);
      }
      setStep('customize');
    } catch (err) {
      console.error("Upload failed", err);
      alert("Ошибка при загрузке фото. Попробуйте еще раз.");
    } finally {
      setUploading(false);
    }
  };

  const handleConfirm = async () => {
    if (!uploadData) return;
    setConfirming(true);

    if (window.Telegram?.WebApp?.HapticFeedback) {
      window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
    }

    try {
      await axios.post(
        `${apiBase}/photos/confirm`,
        {
          post_id: uploadData.post_id,
          animal_type: selectedType,
          is_auto_scheduled: isAutoScheduled,
          schedule_time: !isAutoScheduled && customScheduleTime ? customScheduleTime : null,
        },
        {
          headers: { Authorization: `Bearer ${token}` },
        }
      );

      setStep('success');
    } catch (err) {
      console.error("Confirm failed", err);
      alert("Ошибка при подтверждении. Попробуйте ещё раз.");
    } finally {
      setConfirming(false);
    }
  };

  const resetForm = () => {
    setSelectedFile(null);
    setPreviewUrl(null);
    setUploadData(null);
    setSelectedType('Кот');
    setIsAutoScheduled(true);
    setCustomScheduleTime('');
    setStep('upload');
  };

  return (
    <div className="animate-fade-in" style={{ padding: 16 }}>
      <div style={{ textAlign: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 800 }}>Предложить фото животного 🐾</h2>
        <p style={{ fontSize: 13, color: 'var(--hint-color)', marginTop: 4 }}>
          {step === 'upload' && 'Загрузите фото, чтобы нейросеть проверила его'}
          {step === 'customize' && 'Проверьте результат и выберите параметры публикации'}
          {step === 'success' && 'Фото отправлено модераторам!'}
        </p>
      </div>

      {/* STEP 1: UPLOAD */}
      {step === 'upload' && (
        <div>
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
                  style={{ width: '100%', maxHeight: '220px', objectFit: 'contain', borderRadius: 12, marginBottom: 12 }}
                />
                <span style={{ fontSize: 13, color: '#38bdf8', fontWeight: 600 }}>Нажмите, чтобы изменить файл</span>
              </div>
            ) : (
              <div>
                <UploadCloud size={44} color="#a855f7" style={{ marginBottom: 8 }} />
                <h4 style={{ fontSize: 15, fontWeight: 700 }}>Выберите фото с устройства</h4>
                <p style={{ fontSize: 12, color: 'var(--hint-color)', marginTop: 4 }}>Поддерживаются JPG, PNG, WebP, HEIC</p>
              </div>
            )}
          </div>

          <button
            className="btn-primary"
            onClick={handleUpload}
            disabled={!selectedFile || uploading}
            style={{
              width: '100%',
              opacity: !selectedFile || uploading ? 0.5 : 1,
            }}
          >
            {uploading ? 'Загрузка и анализ ИИ...' : 'Загрузить и проанализировать 🚀'}
          </button>
        </div>
      )}

      {/* STEP 2: CUSTOMIZE & CONFIRM */}
      {step === 'customize' && uploadData && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Photo Preview & Warnings */}
          <div className="glass-panel" style={{ padding: 16, textAlign: 'center' }}>
            {previewUrl && (
              <img
                src={previewUrl}
                alt="Uploaded"
                style={{ width: '100%', maxHeight: '240px', objectFit: 'contain', borderRadius: 14, marginBottom: 12 }}
              />
            )}

            {/* Duplicate Warning */}
            {uploadData.duplicate_of_photo_id !== null && (
              <div
                style={{
                  background: 'rgba(234, 179, 8, 0.15)',
                  border: '1px solid rgba(234, 179, 8, 0.4)',
                  padding: 12,
                  borderRadius: 12,
                  marginBottom: 12,
                  textAlign: 'left',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 10,
                }}
              >
                <AlertTriangle color="#facc15" size={22} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  <span style={{ fontSize: 13, fontWeight: 700, color: '#facc15' }}>Возможный дубликат!</span>
                  <p style={{ fontSize: 12, color: 'var(--hint-color)', marginTop: 2 }}>
                    Это фото уже похоже на существующее фото #{uploadData.duplicate_of_photo_id} в канале.
                  </p>
                </div>
              </div>
            )}

            {/* AI Comment */}
            {uploadData.ai_comment && (
              <div
                style={{
                  background: 'rgba(168, 85, 247, 0.15)',
                  border: '1px solid rgba(168, 85, 247, 0.3)',
                  padding: 12,
                  borderRadius: 12,
                  textAlign: 'left',
                }}
              >
                <span style={{ fontSize: 12, fontWeight: 700, color: '#c084fc', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Sparkles size={14} /> Ответ нейросети Gemini:
                </span>
                <p style={{ fontSize: 13, marginTop: 4, fontStyle: 'italic' }}>"{uploadData.ai_comment}"</p>
              </div>
            )}
          </div>

          {/* Animal Type Selector */}
          <div className="glass-panel" style={{ padding: 16 }}>
            <h4 style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>Категория животного:</h4>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {animalTypes.map((type) => {
                const isSelected = selectedType === type.name;
                return (
                  <button
                    key={type.id}
                    onClick={() => setSelectedType(type.name)}
                    style={{
                      background: isSelected ? 'var(--accent-gradient)' : 'rgba(30, 41, 59, 0.7)',
                      color: isSelected ? '#ffffff' : 'var(--text-color)',
                      border: isSelected ? 'none' : '1px solid var(--glass-border)',
                      padding: '8px 14px',
                      borderRadius: 12,
                      fontSize: 13,
                      fontWeight: 600,
                      cursor: 'pointer',
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

          {/* Schedule Picker */}
          <div className="glass-panel" style={{ padding: 16 }}>
            <h4 style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>Время публикации:</h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <button
                onClick={() => setIsAutoScheduled(true)}
                style={{
                  background: isAutoScheduled ? 'rgba(56, 189, 248, 0.2)' : 'rgba(30, 41, 59, 0.5)',
                  border: isAutoScheduled ? '1px solid #38bdf8' : '1px solid var(--glass-border)',
                  color: 'white',
                  padding: 12,
                  borderRadius: 12,
                  textAlign: 'left',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                }}
              >
                <Clock size={20} color="#38bdf8" />
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>⚡️ Автоматическое расписание</div>
                  <div style={{ fontSize: 11, color: 'var(--hint-color)' }}>Поставит фото в ближайший свободный слот очереди</div>
                </div>
              </button>

              <button
                onClick={() => setIsAutoScheduled(false)}
                style={{
                  background: !isAutoScheduled ? 'rgba(56, 189, 248, 0.2)' : 'rgba(30, 41, 59, 0.5)',
                  border: !isAutoScheduled ? '1px solid #38bdf8' : '1px solid var(--glass-border)',
                  color: 'white',
                  padding: 12,
                  borderRadius: 12,
                  textAlign: 'left',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                }}
              >
                <Calendar size={20} color="#38bdf8" />
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>📅 Задать дату и время вручную</div>
                  <div style={{ fontSize: 11, color: 'var(--hint-color)' }}>Выберите точно, когда должно выйти фото</div>
                </div>
              </button>

              {!isAutoScheduled && (
                <div style={{ marginTop: 6 }}>
                  <input
                    type="datetime-local"
                    value={customScheduleTime}
                    onChange={(e) => setCustomScheduleTime(e.target.value)}
                    style={{
                      width: '100%',
                      padding: 10,
                      borderRadius: 10,
                      background: 'rgba(15, 23, 42, 0.8)',
                      border: '1px solid var(--glass-border)',
                      color: 'white',
                      fontSize: 14,
                    }}
                  />
                </div>
              )}
            </div>
          </div>

          {/* Action Buttons */}
          <button className="btn-primary" onClick={handleConfirm} disabled={confirming} style={{ width: '100%' }}>
            {confirming ? 'Отправка...' : 'Отправить на модерацию 🚀'}
          </button>

          <button
            onClick={resetForm}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--hint-color)',
              fontSize: 13,
              cursor: 'pointer',
              padding: 8,
            }}
          >
            Сбросить и выбрать другое фото
          </button>
        </div>
      )}

      {/* STEP 3: SUCCESS */}
      {step === 'success' && (
        <div className="glass-panel" style={{ padding: 24, textAlign: 'center' }}>
          <CheckCircle size={56} color="#34d399" style={{ marginBottom: 12 }} />
          <h3 style={{ fontSize: 20, fontWeight: 800, marginBottom: 8 }}>Отлично! Заявка создана!</h3>
          <p style={{ fontSize: 14, color: 'var(--hint-color)', marginBottom: 20 }}>
            Фотография и выбранные параметры отправлены модераторам бота. Вы получите уведомление при решении!
          </p>

          <button className="btn-primary" onClick={resetForm} style={{ width: '100%' }}>
            Предложить ещё одно фото 🐾
          </button>
        </div>
      )}
    </div>
  );
};
