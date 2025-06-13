import React, { useState, useEffect } from 'react';
import './MusicModal.css';

interface MusicFile {
    id: number;
    display_name: string;
    file_type: string;
}

interface MusicModalProps {
    enterpriseId: string;
    onClose: () => void;
    onSelect: (file: MusicFile) => void;
    modalTitle: string;
    fileType: 'hold' | 'start';
}

const MusicModal: React.FC<MusicModalProps> = ({ enterpriseId, onClose, onSelect, modalTitle, fileType }) => {
    const [files, setFiles] = useState<MusicFile[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchMusicFiles = async () => {
            setLoading(true);
            try {
                const response = await fetch(`/enterprise/${enterpriseId}/audiofiles`);
                if (!response.ok) {
                    throw new Error('Failed to fetch music files');
                }
                const data: MusicFile[] = await response.json();
                const filteredFiles = data.filter(file => file.file_type === fileType);
                setFiles(filteredFiles);
            } catch (error) {
                console.error(error);
                // Тут можно показать ошибку пользователю
            } finally {
                setLoading(false);
            }
        };

        fetchMusicFiles();
    }, [enterpriseId, fileType]);

    const handleSelectClick = (file: MusicFile) => {
        onSelect(file);
        onClose();
    };

    return (
        <div className="music-modal-overlay" onClick={onClose}>
            <div className="music-modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="music-modal-header">
                    <h3>{modalTitle}</h3>
                </div>
                <div className="music-modal-body">
                    {loading ? (
                        <p>Загрузка...</p>
                    ) : (
                        <table className="music-files-table">
                            <tbody>
                                {files.map((file) => (
                                    <tr key={file.id}>
                                        <td>
                                            <button onClick={() => handleSelectClick(file)} className="select-btn">
                                                Выбрать
                                            </button>
                                        </td>
                                        <td>{file.display_name}</td>
                                        <td style={{ width: '140px' }}>
                                            <audio
                                                controls
                                                controlsList="nodownload"
                                                src={`/audiofile/${file.id}`}
                                                style={{ width: '120px', height: '32px', verticalAlign: 'middle' }}
                                            />
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
                <div className="music-modal-footer">
                    <button onClick={onClose} className="close-button">Закрыть</button>
                </div>
            </div>
        </div>
    );
};

export default MusicModal; 