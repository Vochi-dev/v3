import React, { useEffect, useState, useRef } from 'react';
import './IncomingCallModal.css';
import { Line } from './types';

interface IncomingCallModalProps {
    enterpriseId: string;
    schemaName: string;
    initialSelectedLines: Set<string>;
    onClose: () => void;
    onConfirm: (selectedLineIds: Set<string>) => void;
}

const IncomingCallModal: React.FC<IncomingCallModalProps> = ({ 
    enterpriseId, 
    schemaName, 
    initialSelectedLines,
    onClose,
    onConfirm 
}) => {
    const [lines, setLines] = useState<Line[]>([]);
    const [selectedLines, setSelectedLines] = useState<Set<string>>(initialSelectedLines);
    const [filterLine, setFilterLine] = useState('');
    const [filterSchema, setFilterSchema] = useState('');
    const modalRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        setSelectedLines(initialSelectedLines);
    }, [initialSelectedLines]);

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
            });
    }, [enterpriseId]);

    const handleConfirm = () => {
        onConfirm(selectedLines);
        onClose();
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
        <div className="incoming-call-modal-overlay">
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
                    <button className="save-button" onClick={handleConfirm}>OK</button>
                </footer>
            </div>
        </div>
    );
};

export default IncomingCallModal; 