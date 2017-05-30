from io import StringIO
from unittest import TestCase

from micall.hivdb.hivdb import read_aminos, write_resistance


class ReadAminosTest(TestCase):
    def test_simple(self):
        amino_csv = StringIO("""\
seed,region,q-cutoff,query.nuc.pos,refseq.aa.pos,\
A,C,D,E,F,G,H,I,K,L,M,N,P,Q,R,S,T,V,W,Y,*,X,partial,del,ins,clip,g2p_overlap
R1-seed,R1,15,1,1,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,4,2,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,7,3,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
""")
        min_fraction = 0.2
        expected_aminos = [('R1', [['K'], ['F'], ['A']])]

        aminos = list(read_aminos(amino_csv, min_fraction))

        self.assertEqual(expected_aminos, aminos)

    def test_mixtures(self):
        amino_csv = StringIO("""\
seed,region,q-cutoff,query.nuc.pos,refseq.aa.pos,\
A,C,D,E,F,G,H,I,K,L,M,N,P,Q,R,S,T,V,W,Y,*,X,partial,del,ins,clip,g2p_overlap
R1-seed,R1,15,1,1,1,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,4,2,2,0,0,0,8,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,,3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
""")
        min_fraction = 0.2
        expected_aminos = [('R1', [['K'], ['A', 'F'], []])]

        aminos = list(read_aminos(amino_csv, min_fraction))

        self.assertEqual(expected_aminos, aminos)

    def test_no_coverage(self):
        amino_csv = StringIO("""\
seed,region,q-cutoff,query.nuc.pos,refseq.aa.pos,\
A,C,D,E,F,G,H,I,K,L,M,N,P,Q,R,S,T,V,W,Y,*,X,partial,del,ins,clip,g2p_overlap
R1-seed,R1,15,1,1,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,4,2,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,,3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
""")
        min_fraction = 0.2
        expected_aminos = [('R1', [['K'], ['F'], []])]

        aminos = list(read_aminos(amino_csv, min_fraction))

        self.assertEqual(expected_aminos, aminos)

    def test_multiple_regions(self):
        amino_csv = StringIO("""\
seed,region,q-cutoff,query.nuc.pos,refseq.aa.pos,\
A,C,D,E,F,G,H,I,K,L,M,N,P,Q,R,S,T,V,W,Y,*,X,partial,del,ins,clip,g2p_overlap
R1-seed,R1,15,1,1,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,4,2,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,,3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R2-seed,R2,15,1,1,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R2-seed,R2,15,4,2,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R2-seed,R2,15,7,3,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
""")
        min_fraction = 0.2
        expected_aminos = [('R1', [['K'], ['F'], []]),
                           ('R2', [['K'], ['F'], ['C']])]

        aminos = list(read_aminos(amino_csv, min_fraction))

        self.assertEqual(expected_aminos, aminos)

    def test_reported_regions(self):
        amino_csv = StringIO("""\
seed,region,q-cutoff,query.nuc.pos,refseq.aa.pos,\
A,C,D,E,F,G,H,I,K,L,M,N,P,Q,R,S,T,V,W,Y,*,X,partial,del,ins,clip,g2p_overlap
R1-seed,R1,15,1,1,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,4,2,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R1-seed,R1,15,,3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R2-seed,R2,15,1,1,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R2-seed,R2,15,4,2,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
R2-seed,R2,15,7,3,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
""")
        min_fraction = 0.2
        reported_regions = ['R2']
        expected_aminos = [('R2', [['K'], ['F'], ['C']])]

        aminos = list(read_aminos(amino_csv,
                                  min_fraction,
                                  reported_regions=reported_regions))

        self.assertEqual(expected_aminos, aminos)

    def test_deletions(self):
        amino_csv = StringIO("""\
seed,region,q-cutoff,query.nuc.pos,refseq.aa.pos,\
A,C,D,E,F,G,H,I,K,L,M,N,P,Q,R,S,T,V,W,Y,*,X,partial,del,ins,clip,g2p_overlap
R1-seed,R1,15,1,1,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,0,0
R1-seed,R1,15,4,2,0,0,0,0,8,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2,0,0,0
R1-seed,R1,15,,3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
""")
        min_fraction = 0.2
        expected_aminos = [('R1', [['K'], ['F', 'd'], []])]

        aminos = list(read_aminos(amino_csv, min_fraction))

        self.assertEqual(expected_aminos, aminos)

    def test_insertions(self):
        amino_csv = StringIO("""\
seed,region,q-cutoff,query.nuc.pos,refseq.aa.pos,\
A,C,D,E,F,G,H,I,K,L,M,N,P,Q,R,S,T,V,W,Y,*,X,partial,del,ins,clip,g2p_overlap
R1-seed,R1,15,1,1,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,0
R1-seed,R1,15,4,2,0,0,0,0,8,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2,0,0
R1-seed,R1,15,,3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
""")
        min_fraction = 0.2
        expected_aminos = [('R1', [['K'], ['F', 'i'], []])]

        aminos = list(read_aminos(amino_csv, min_fraction))

        self.assertEqual(expected_aminos, aminos)

    def test_stop_codons(self):
        amino_csv = StringIO("""\
seed,region,q-cutoff,query.nuc.pos,refseq.aa.pos,\
A,C,D,E,F,G,H,I,K,L,M,N,P,Q,R,S,T,V,W,Y,*,X,partial,del,ins,clip,g2p_overlap
R1-seed,R1,15,1,1,0,0,0,0,0,0,0,0,9,0,0,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0
R1-seed,R1,15,4,2,0,0,0,0,8,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2,0,0,0,0,0,0
R1-seed,R1,15,,3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
""")
        min_fraction = 0.2
        expected_aminos = [('R1', [['K'], ['F'], []])]

        aminos = list(read_aminos(amino_csv, min_fraction))

        self.assertEqual(expected_aminos, aminos)


class WriteResistanceTest(TestCase):
    def test_simple(self):
        aminos = [('RT', [['A']] * 40 + [['L'], ['A']])]
        resistance_csv = StringIO()
        expected_report = """\
region,drug,level_name,level,score
RT,3TC,Susceptible,1,0.0
RT,ABC,Susceptible,1,5.0
RT,AZT,Low-Level Resistance,3,15.0
RT,D4T,Low-Level Resistance,3,15.0
RT,DDI,Potential Low-Level Resistance,2,10.0
RT,FTC,Susceptible,1,0.0
RT,TDF,Susceptible,1,5.0
RT,EFV,Susceptible,1,0.0
RT,ETR,Susceptible,1,0.0
RT,NVP,Susceptible,1,0.0
RT,RPV,Susceptible,1,0.0
"""

        write_resistance(resistance_csv, aminos)

        self.assertEqual(expected_report, resistance_csv.getvalue())
