import React, { useState } from 'react';
import './PatternCheckModal.css';
import AddPatternModal from './AddPatternModal';

interface PatternCheckModalProps {
    onClose: () => void;
    onConfirm: () => void;
    onDelete: () => void;
}

const PatternCheckModal: React.FC<PatternCheckModalProps> = ({ onClose, onConfirm, onDelete }) => {
    const [isAddModalOpen, setAddModalOpen] = useState(false);

    const openAddModal = () => setAddModalOpen(true);
    const closeAddModal = () => setAddModalOpen(false);

    return (
        <>
            <div className="modal-overlay">
                <div className="modal-content pattern-check-modal">
                    <h2>Проверка по шаблону</h2>
                    <div className="table-container">
                        <table>
                            <thead>
                                <tr>
                                    <th>Название</th>
                                    <th>Шаблон</th>
                                    <th></th>
                                </tr>
                            </thead>
                            <tbody>
                                {/* Rows will be added here later */}
                            </tbody>
                        </table>
                    </div>
                    <div className="add-template-button-container">
                        <button className="add-template-button" onClick={openAddModal}>+ Добавить шаблон</button>
                    </div>
                    <div className="modal-footer">
                        <button onClick={onDelete} className="delete-button">Удалить</button>
                        <div className="footer-right-buttons">
                            <button onClick={onConfirm} className="ok-button">OK</button>
                            <button onClick={onClose} className="cancel-button">Отмена</button>
                        </div>
                    </div>
                </div>
            </div>
            <AddPatternModal
                isOpen={isAddModalOpen}
                onClose={closeAddModal}
            />
        </>
    );
};

export default PatternCheckModal; 