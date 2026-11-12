-- Bootstrap only in the disposable Compose database, using its administrator.
-- Application roles cannot create, replace, or edit this identity.
CREATE SCHEMA test_authority;
REVOKE ALL ON SCHEMA test_authority FROM PUBLIC;
CREATE TABLE test_authority.database_identity (
    singleton boolean PRIMARY KEY CHECK (singleton),
    database_oid oid NOT NULL,
    database_name name NOT NULL,
    cluster_identifier bigint NOT NULL
);
INSERT INTO test_authority.database_identity
SELECT true, d.oid, d.datname, c.system_identifier
FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_control_system() c
WHERE d.datname = current_database();
CREATE FUNCTION test_authority.require_test_database() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM test_authority.database_identity i
        JOIN pg_catalog.pg_database d ON d.oid=i.database_oid AND d.datname=i.database_name
        CROSS JOIN pg_catalog.pg_control_system() c
        WHERE i.singleton AND d.datname=current_database()
          AND i.cluster_identifier=c.system_identifier
    ) THEN
        RAISE EXCEPTION 'database is not the bound TEST instance';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION test_authority.require_test_database() FROM PUBLIC;
-- Grants to the five concrete owner roles are issued by bootstrap, not PUBLIC.
