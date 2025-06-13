import React, { useState } from 'react';
import './GreetingModal.css';
import MusicModal from './MusicModal';

interface MusicFile {
    id: number;
    display_name: string;
}

interface GreetingModalProps {
    enterpriseId: string;
    onClose: () => void;
}

const GreetingModal: React.FC<GreetingModalProps> = ({ enterpriseId, onClose }) => {
    const [isMusicModalOpen, setIsMusicModalOpen] = useState(false);
    const [selectedFile, setSelectedFile] = useState<MusicFile | null>(null);

    const handleSelectFile = (file: MusicFile) => {
        setSelectedFile(file);
        setIsMusicModalOpen(false);
    };

    return (
        <>
            <div className="greeting-modal-overlay" onClick={onClose}>
                <div className="greeting-modal-content" onClick={(e) => e.stopPropagation()}>
                    <div className="greeting-modal-header">
                        <h3>Приветствие</h3>
                        <button onClick={onClose} className="close-button">&times;</button>
                    </div>
                    <div className="greeting-modal-body">
                        <div className="greeting-file-selector">
                            <span>Звуковое приветствие</span>
                            <button 
                                className="choose-file-button"
                                onClick={() => setIsMusicModalOpen(true)}
                            >
                                Выберите файл
                            </button>
                            {selectedFile && (
                                <span className="selected-file-name">{selectedFile.display_name}</span>
                            )}
                        </div>
                    </div>
                    <div className="greeting-modal-footer">
                        <div className="footer-buttons-left">
                            <button className="delete-button">Удалить</button>
                        </div>
                        <div className="footer-buttons-right">
                            <button className="cancel-button" onClick={onClose}>Отмена</button>
                            <button className="ok-button">ОК</button>
                        </div>
                    </div>
                </div>
            </div>

            {isMusicModalOpen && (
                <MusicModal
                    enterpriseId={enterpriseId}
                    onClose={() => setIsMusicModalOpen(false)}
                    onSelect={handleSelectFile}
                    modalTitle="Выберите файл приветствия"
                    fileType="start"
                />
            )}
        </>
    );
};

export default GreetingModal; 