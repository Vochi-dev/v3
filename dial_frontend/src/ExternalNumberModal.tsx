import React, { useState, useEffect } from 'react';
import './ExternalNumberModal.css';
import { Line } from './types';

interface ExternalNumberModalProps {
    isOpen: boolean;
    onClose: () => void;
    onDelete?: () => void; 
    onConfirm: (lines: { line_id: string, priority: number }[], allLines: Line[]) => void;
    enterpriseId: string;
    initialData?: { line_id: string, priority: number }[];
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
  enterpriseId,
  initialData = []
}) => {
    const [tableRows, setTableRows] = useState<TableRow[]>([]);
    const [availableLines, setAvailableLines] = useState<Line[]>([]);

    useEffect(() => {
        if (isOpen && enterpriseId) {
            fetch(`/dial/api/enterprises/${enterpriseId}/lines`)
                .then(res => res.json())
                .then((data: Line[]) => {
                    const isGsm = (line: Line) => /^\d+$/.test(line.id);

                    const sortedData = [...data].sort((a, b) => {
                        const aIsGsm = isGsm(a);
                        const bIsGsm = isGsm(b);

                        if (aIsGsm && !bIsGsm) return -1;
                        if (!aIsGsm && bIsGsm) return 1;
                        
                        const aId = parseInt(a.id.replace(/\D/g, ''), 10);
                        const bId = parseInt(b.id.replace(/\D/g, ''), 10);
                        return aId - bId;
                    });
                    setAvailableLines(sortedData);
                })
                .catch(error => console.error("Ошибка загрузки линий:", error));
        }
    }, [isOpen, enterpriseId]);
    
    useEffect(() => {
        if (isOpen) {
            const transformedData = (initialData || []).map((item, index) => ({
                id: Date.now() + index,
                selectedLine: item.line_id,
                priority: item.priority.toString(),
            }));
            setTableRows(transformedData);
        }
    }, [initialData, isOpen]);


    if (!isOpen) {
        return null;
    }

    const handleAddRow = () => {
        const maxPriority = tableRows.length > 0
            ? Math.max(0, ...tableRows.map(row => parseInt(row.priority, 10)).filter(p => !isNaN(p)))
            : 0;
        const newRow: TableRow = {
            id: Date.now(),
            selectedLine: '',
            priority: (maxPriority + 1).toString(),
        };
        setTableRows([...tableRows, newRow]);
    };

    const handleDeleteRow = (id: number) => {
        setTableRows(tableRows.filter(row => row.id !== id));
    };

    const handleRowChange = (id: number, field: keyof TableRow, value: string) => {
        setTableRows(tableRows.map(row => (row.id === id ? { ...row, [field]: value } : row)));
    };
    
    const handleConfirmClick = () => {
        const linesToSave = tableRows
            .map(row => ({
                line_id: row.selectedLine,
                priority: parseInt(row.priority, 10),
            }))
            .filter(line => line.line_id && !isNaN(line.priority));
        
        onConfirm(linesToSave, availableLines);
        onClose();
    };

    const selectedLineIds = new Set(tableRows.map(row => row.selectedLine));

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
                                                {availableLines.map(line => {
                                                    const isSelected = selectedLineIds.has(line.id);
                                                    const isCurrentlySelectedInThisRow = row.selectedLine === line.id;
                                                    return (
                                                        <option 
                                                            key={line.id} 
                                                            value={line.id} 
                                                            disabled={isSelected && !isCurrentlySelectedInThisRow}
                                                        >
                                                            {line.display_name}
                                                        </option>
                                                    );
                                                })}
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
                    <button onClick={handleConfirmClick} className="btn-confirm">Сохранить</button>
                </div>
            </div>
        </div>
    );
};

export default ExternalNumberModal; 