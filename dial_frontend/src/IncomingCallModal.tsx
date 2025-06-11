import React, { useState, useEffect } from 'react';
import './IncomingCallModal.css';

interface Line {
    id: string;
    display_name: string;
    in_schema: string | null;
}

interface IncomingCallModalProps {
    isOpen: boolean;
    onClose: () => void;
    enterpriseId: string;
    schemaId: string;
    schemaName: string;
    // We will need to pass the schema's currently associated lines here
    // For now, we'll fetch them, but a better approach would be to pass them as props.
}

const IncomingCallModal: React.FC<IncomingCallModalProps> = ({ isOpen, onClose, enterpriseId, schemaId, schemaName }) => {
    const [lines, setLines] = useState<Line[]>([]);
    const [selectedLines, setSelectedLines] = useState<Set<string>>(new Set());
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [filterLine, setFilterLine] = useState('');
    const [filterSchema, setFilterSchema] = useState('');

    useEffect(() => {
        if (isOpen && enterpriseId) {
            setLoading(true);
            fetch(`/dial/api/enterprises/${enterpriseId}/lines`)
                .then(res => {
                    if (!res.ok) throw new Error('Failed to fetch lines');
                    return res.json();
                })
                .then((data: Line[]) => {
                    setLines(data);
                    // Initialize selection based on which lines belong to the current schema
                    const currentSchemaLines = new Set(
                        data.filter(line => line.in_schema === schemaName).map(line => line.id)
                    );
                    setSelectedLines(currentSchemaLines);
                    setError(null);
                })
                .catch(err => {
                    console.error(err);
                    setError(err.message);
                })
                .finally(() => setLoading(false));
        }
    }, [isOpen, enterpriseId, schemaName]);

    const handleCheckboxChange = (lineId: string) => {
        setSelectedLines(prev => {
            const newSelection = new Set(prev);
            if (newSelection.has(lineId)) {
                newSelection.delete(lineId);
            } else {
                newSelection.add(lineId);
            }
            return newSelection;
        });
    };
    
    const handleSave = () => {
        fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schemaId}/assign_lines`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Array.from(selectedLines)),
        })
        .then(res => {
            if (!res.ok) throw new Error('Failed to save lines');
            onClose(); // Close modal on success
        })
        .catch(err => {
            console.error(err);
            setError(err.message);
        });
    };

    if (!isOpen) {
        return null;
    }

    const filteredLines = lines.filter(line =>
        line.display_name.toLowerCase().includes(filterLine.toLowerCase()) &&
        (line.in_schema || '').toLowerCase().includes(filterSchema.toLowerCase())
    );

    return (
        <div className="modal-overlay">
            <div className="modal-content">
                <button onClick={onClose} className="modal-close-btn">&times;</button>
                <h2>Настройка входящих линий</h2>
                {loading && <p>Загрузка линий...</p>}
                {error && <p className="error-message">Ошибка: {error}</p>}
                
                <table className="lines-table">
                    <thead>
                        <tr>
                            <th></th>
                            <th>
                                Линия
                                <input 
                                    type="text" 
                                    value={filterLine}
                                    onChange={e => setFilterLine(e.target.value)}
                                    placeholder="Фильтр по линии"
                                />
                            </th>
                            <th>
                                Входящая схема
                                <input 
                                    type="text" 
                                    value={filterSchema}
                                    onChange={e => setFilterSchema(e.target.value)}
                                    placeholder="Фильтр по схеме"
                                />
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {filteredLines.map(line => (
                            <tr key={line.id} className={line.in_schema && line.in_schema !== schemaName ? 'line-assigned-other' : ''}>
                                <td>
                                    <input 
                                        type="checkbox"
                                        checked={selectedLines.has(line.id)}
                                        onChange={() => handleCheckboxChange(line.id)}
                                    />
                                </td>
                                <td>{line.display_name}</td>
                                <td>{line.in_schema}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>

                <div className="modal-actions">
                    <button onClick={handleSave} className="ok-btn">OK</button>
                    <button onClick={onClose} className="cancel-btn">Отмена</button>
                </div>
            </div>
        </div>
    );
};

export default IncomingCallModal; 