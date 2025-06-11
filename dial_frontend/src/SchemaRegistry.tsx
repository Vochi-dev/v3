import { useState, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import './SchemaRegistry.css';

function SchemaRegistry() {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [searchParams] = useSearchParams();
    const enterpriseNumber = searchParams.get('enterprise');

    useEffect(() => {
        if (!enterpriseNumber) {
            setError('Enterprise number is missing from URL');
            setLoading(false);
            return;
        }

        const fetchSchemas = async () => {
            try {
                setLoading(true);
                const response = await fetch(`api/v1/enterprises/${enterpriseNumber}/schemas`);
                if (!response.ok) {
                    throw new Error('Failed to fetch schemas');
                }
                // Пока не используем данные
                // const data = await response.json();
                // setSchemas(data);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };

        fetchSchemas();
    }, [enterpriseNumber]);

    if (loading) {
        return <div>Loading...</div>;
    }

    if (error) {
        return <div className="error-message">Error: {error}</div>;
    }

    return (
        <div className="schema-registry-container">
            <div className="registry-header">
                <h1>Входящие схемы</h1>
                <Link to={`/editor/new?enterprise=${enterpriseNumber}`} className="btn btn-add">+</Link>
            </div>
            {/* Пока оставим только кнопку создания */}
        </div>
    );
}

export default SchemaRegistry;