import sys; sys.path.insert(0, __file__.rsplit('/tests/', 1)[0])
import maestro_qagate as G
bad = ["Veredicto: não está pronto para o NAS.\nMust-fix: M1 auth ... M6 backups\nLacunas funcionais: pulso, chats.\nMelhorias: ...",
       "Refiz o logo.\nPendente: pulso e chats. Diz-me onde é o deploy.",
       "Feito o essencial. Queres que avance com o deploy?"]
good = ["Implementado e validado: 42 testes passam, deploy em produção confirmado (curl mostra a versão nova). Nada pendente.",
        "Veredicto: pronto. Sem must-fix. Todos os achados anteriores corrigidos e verificados."]
for t in bad: assert G.check(t), t
for t in good: assert not G.check(t), (t, G.check(t))
assert 'CICLO 1/3' in G.redo_note(bad[0], 1)
print('ok')
