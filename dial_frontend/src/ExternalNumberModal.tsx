import React, { useState } from 'react';
import './ExternalNumberModal.css';

interface ExternalNumberModalProps {
    isOpen: boolean;
    onClose: () => void;
    onDelete?: () => void; 
    onConfirm: () => void;
}

interface TableRow {
    id: number;
    selectedLine: string;
    priority: string;
}

const ExternalNumberModal: React.FC<ExternalNumberModalProps> = ({ 
  isOpen, 
  onClose,
  onDelete,
  onConfirm,
}) => {
    const [tableRows, setTableRows] = useState<TableRow[]>([]);

    if (!isOpen) {
        return null;
    }

    const handleAddRow = () => {
        const newRow: TableRow = {
            id: Date.now(), // Простое уникальное ID
            selectedLine: '',
            priority: '1',
        };
        setTableRows([...tableRows, newRow]);
    };

    const handleDeleteRow = (id: number) => {
        setTableRows(tableRows.filter(row => row.id !== id));
    };

    const handleRowChange = (id: number, field: keyof TableRow, value: string) => {
        setTableRows(tableRows.map(row => (row.id === id ? { ...row, [field]: value } : row)));
    };
    
    const handleConfirm = () => {
        // TODO: Добавить логику сохранения данных
        console.log('Сохраняемые строки:', tableRows);
        onConfirm();
        onClose();
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Выбор внешнего номера</h3>
                    <button onClick={onClose} className="close-button">&times;</button>
                </div>
                <div className="modal-body">
                    <table className="external-lines-table">
                        <thead>
                            <tr>
                                <th>Внешняя линия</th>
                                <th>Приоритет</th>
                                <th style={{ width: '50px' }}></th>
                            </tr>
                        </thead>
                        <tbody>
                            {tableRows.length === 0 ? (
                                <tr>
                                    <td colSpan={3} style={{ textAlign: 'center', padding: '20px' }}>
                                        Нажмите "+ Добавить линию", чтобы начать.
                                    </td>
                                </tr>
                            ) : (
                                tableRows.map((row) => (
                                    <tr key={row.id}>
                                        <td>
                                            <select
                                                value={row.selectedLine}
                                                onChange={(e) => handleRowChange(row.id, 'selectedLine', e.target.value)}
                                                className="line-select"
                                            >
                                                <option value="" disabled>Выберите линию...</option>
                                                {/* TODO: Заполнить реальными данными */}
                                            </select>
                                        </td>
                                        <td>
                                            <input
                                                type="number"
                                                min="1"
                                                value={row.priority}
                                                onChange={(e) => handleRowChange(row.id, 'priority', e.target.value)}
                                                className="priority-input"
                                            />
                                        </td>
                                        <td>
                                            <button onClick={() => handleDeleteRow(row.id)} className="btn-delete-row">
                                                &times;
                                            </button>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                    <div className="add-line-container">
                        <button onClick={handleAddRow} className="btn-add-line">
                            + Добавить линию
                        </button>
                    </div>
                </div>
                <div className="modal-footer">
                    <button onClick={onClose} className="btn-cancel">Отмена</button>
                    {onDelete && <button onClick={onDelete} className="btn-delete">Удалить узел</button>}
                    <button onClick={handleConfirm} className="btn-confirm">Сохранить</button>
                </div>
            </div>
        </div>
    );
};

export default ExternalNumberModal; 