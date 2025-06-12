import React, { useEffect, useState, useRef } from 'react';
import './IncomingCallModal.css';

interface Line {
    id: string;
    display_name: string;
    in_schema: string | null;
}

interface IncomingCallModalProps {
    enterpriseId: string;
    schemaId: string;
    schemaName: string;
    onClose: () => void;
}

const IncomingCallModal: React.FC<IncomingCallModalProps> = ({ enterpriseId, schemaId, schemaName, onClose }) => {
    const [lines, setLines] = useState<Line[]>([]);
    const [selectedLines, setSelectedLines] = useState<Set<string>>(new Set());
    const [filterLine, setFilterLine] = useState('');
    const [filterSchema, setFilterSchema] = useState('');
    const modalRef = useRef<HTMLDivElement>(null);

    // Click outside handler
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (modalRef.current && !modalRef.current.contains(event.target as Node)) {
                onClose();
            }
        };

        // Bind the event listener
        document.addEventListener("mousedown", handleClickOutside);
        return () => {
            // Unbind the event listener on clean up
            document.removeEventListener("mousedown", handleClickOutside);
        };
    }, [modalRef, onClose]);

    useEffect(() => {
        fetch(`/dial/api/enterprises/${enterpriseId}/lines`)
            .then(res => res.json())
            .then((data: Line[]) => {
                setLines(data);
                const initiallySelected = new Set(data.filter(line => line.in_schema === schemaName).map(line => line.id));
                setSelectedLines(initiallySelected);
            });
    }, [enterpriseId, schemaName]);

    const handleSave = () => {
        fetch(`/dial/api/enterprises/${enterpriseId}/schemas/${schemaId}/assign_lines`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Array.from(selectedLines)),
        })
        .then(res => {
            if (res.ok) {
                alert('Линии сохранены');
                onClose();
            } else {
                alert('Ошибка сохранения');
            }
        });
    };

    const handleSelectLine = (lineId: string, isSelected: boolean) => {
        const newSelection = new Set(selectedLines);
        if (isSelected) {
            newSelection.add(lineId);
        } else {
            newSelection.delete(lineId);
        }
        setSelectedLines(newSelection);
    };

    const filteredLines = lines.filter(line =>
        line.display_name.toLowerCase().includes(filterLine.toLowerCase()) &&
        (line.in_schema || '').toLowerCase().includes(filterSchema.toLowerCase())
    );

    return (
        <div className="incoming-call-modal" ref={modalRef}>
            <header className="incoming-call-modal-header">
                <h3>Привязка линий к схеме "{schemaName}"</h3>
                <button onClick={onClose} className="incoming-call-modal-close-button">&times;</button>
            </header>
            <main className="incoming-call-modal-body">
                <table className="lines-table">
                    <thead>
                        <tr>
                            <th></th>
                            <th>
                                Линия
                                <input type="text" value={filterLine} onChange={e => setFilterLine(e.target.value)} placeholder="Фильтр..."/>
                            </th>
                            <th>
                                Входящая схема
                                <input type="text" value={filterSchema} onChange={e => setFilterSchema(e.target.value)} placeholder="Фильтр..."/>
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {filteredLines.map(line => (
                            <tr key={line.id}>
                                <td>
                                    <input
                                        type="checkbox"
                                        checked={selectedLines.has(line.id)}
                                        onChange={(e) => handleSelectLine(line.id, e.target.checked)}
                                    />
                                </td>
                                <td>{line.display_name}</td>
                                <td>{line.in_schema}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </main>
            <footer className="incoming-call-modal-footer">
                <button className="cancel-button" onClick={onClose}>Отмена</button>
                <button className="save-button" onClick={handleSave}>OK</button>
            </footer>
        </div>
    );
};

export default IncomingCallModal; 