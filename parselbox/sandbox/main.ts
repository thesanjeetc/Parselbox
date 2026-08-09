import { logger, rpc } from './rpc.ts';
import { PyodideManager } from './sandbox.ts';

async function main() {
  const config = JSON.parse(Deno.env.get('PARSELBOX_CONFIG') || '{}');
  const manager = new PyodideManager(config);

  await manager.start();

  await Deno.permissions.revoke({
    name: 'write',
    path: Deno.env.get('DENO_DIR')!,
  });

  logger.info('deno', 'Sandbox ready');
  rpc.notify('ready', {});

  rpc.register('exec', async (params) => {
    return await manager.execute(params.code);
  });

  await rpc.listen();
}

main().catch((err) => {
  console.error('Critical failure:', err);
  Deno.exit(1);
});
