import React, { useState, useEffect } from 'react';
import './AddPatternModal.css';

interface Template {
    id: number;
    name: string;
    shablon: string;
}

interface AddPatternModalProps {
    isOpen: boolean;
    onClose: () => void;
    // Пока не будет onConfirm, чтобы не трогать родительский компонент
}

const AddPatternModal: React.FC<AddPatternModalProps> = ({ isOpen, onClose }) => {
    const [templates, setTemplates] = useState<Template[]>([]);
    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    const [isLoading, setIsLoading] = useState(false);

    useEffect(() => {
        if (isOpen) {
            setIsLoading(true);
            fetch('/api/templates')
                .then(res => res.ok ? res.json() : Promise.reject(res))
                .then((data: Template[]) => {
                    setTemplates(data);
                })
                .catch(err => console.error("Failed to fetch templates:", err))
                .finally(() => setIsLoading(false));
        }
    }, [isOpen]);

    const handleToggleSelection = (id: number) => {
        setSelectedIds(prev => {
            const newSet = new Set(prev);
            if (newSet.has(id)) {
                newSet.delete(id);
            } else {
                newSet.add(id);
            }
            return newSet;
        });
    };

    if (!isOpen) return null;

    return (
        <div className="modal-overlay">
            <div className="modal-content add-pattern-modal">
                <h2>Добавить шаблоны</h2>
                <div className="table-container">
                    {isLoading ? <p>Загрузка...</p> : (
                        <table>
                            <thead>
                                <tr>
                                    <th></th>
                                    <th>Название</th>
                                    <th>Шаблон</th>
                                </tr>
                            </thead>
                            <tbody>
                                {templates.map(template => (
                                    <tr key={template.id}>
                                        <td>
                                            <input
                                                type="checkbox"
                                                checked={selectedIds.has(template.id)}
                                                onChange={() => handleToggleSelection(template.id)}
                                            />
                                        </td>
                                        <td>{template.name}</td>
                                        <td>{template.shablon}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
                <div className="modal-footer">
                     <button onClick={onClose} className="cancel-button">Отмена</button>
                     <button onClick={onClose} className="ok-button">OK</button>
                </div>
            </div>
        </div>
    );
};

export default AddPatternModal; 