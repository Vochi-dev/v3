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
    onConfirm: (selectedTemplates: Template[]) => void;
}

const AddPatternModal: React.FC<AddPatternModalProps> = ({ isOpen, onClose, onConfirm }) => {
    const [templates, setTemplates] = useState<Template[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (isOpen) {
            setIsLoading(true);
            setError(null);
            fetch('/dial/api/templates')
                .then(res => {
                    if (!res.ok) {
                        throw new Error('Ошибка сети или сервера');
                    }
                    return res.json();
                })
                .then((data: Template[]) => {
                    setTemplates(data);
                })
                .catch(err => {
                    console.error("Ошибка при загрузке шаблонов:", err);
                    setError('Не удалось загрузить шаблоны. Пожалуйста, попробуйте снова.');
                })
                .finally(() => {
                    setIsLoading(false);
                });
        }
    }, [isOpen]);

    if (!isOpen) {
        return null;
    }

    return (
        <div className="modal-overlay">
            <div className="modal-content add-pattern-modal">
                <h2>Добавить шаблоны</h2>
                <div className="add-pattern-modal-content">
                    {isLoading && <p>Загрузка...</p>}
                    {error && <p className="error-message">{error}</p>}
                    {!isLoading && !error && (
                        <table className="templates-table">
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
                                        <td><input type="checkbox" /></td>
                                        <td>{template.name}</td>
                                        <td>{template.shablon}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
                <div className="add-pattern-modal-footer">
                    <button className="cancel-button" onClick={onClose}>Отмена</button>
                    <button className="ok-button" onClick={() => onConfirm([])}>ОК</button>
                </div>
            </div>
        </div>
    );
};

export default AddPatternModal; 