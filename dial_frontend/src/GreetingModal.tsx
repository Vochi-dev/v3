import React, { useState, useEffect } from 'react';
import './GreetingModal.css';
import MusicModal from './MusicModal';

interface GreetingModalProps {
    enterpriseId: string;
    onClose: () => void;
    onConfirm: (data: {
        greetingFile?: { id: number; name: string };
    }) => void;
    initialData?: any;
    onDelete: () => void;
}

const GreetingModal: React.FC<GreetingModalProps> = ({ enterpriseId, onClose, onConfirm, initialData, onDelete }) => {
    const [greetingFile, setGreetingFile] = useState<{ id: number; name: string } | null>(null);
    const [isMusicModalOpen, setIsMusicModalOpen] = useState(false);

    useEffect(() => {
        if (initialData) {
            setGreetingFile(initialData.greetingFile || null);
        }
    }, [initialData]);

    const handleSelectFile = (file: {id: number, display_name: string}) => {
        setGreetingFile({ id: file.id, name: file.display_name });
        setIsMusicModalOpen(false);
    };

    const handleConfirm = () => {
        if (!greetingFile) {
            alert('Необходимо выбрать файл приветствия.');
            return;
        }
        onConfirm({
            greetingFile: greetingFile,
        });
    };

    return (
        <div className="greeting-modal-overlay" onClick={onClose}>
            <div className="greeting-modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="greeting-modal-header">
                    <h3>Приветствие</h3>
                    <button onClick={onClose} className="close-button">&times;</button>
                </div>
                <div className="greeting-modal-body">
                    <div className="file-selection-container">
                        <span className="selected-file-name">{greetingFile?.name || 'Файл не выбран'}</span>
                        <button className="choose-file-button" onClick={() => setIsMusicModalOpen(true)}>Выбрать файл</button>
                    </div>
                </div>
                <div className="greeting-modal-footer">
                    <div className="footer-buttons-left">
                        <button className="delete-button" onClick={onDelete}>Удалить</button>
                    </div>
                    <div className="footer-buttons-right">
                        <button className="cancel-button" onClick={onClose}>Отмена</button>
                        <button className="ok-button" onClick={handleConfirm}>ОК</button>
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
        </div>
    );
};

export default GreetingModal; 