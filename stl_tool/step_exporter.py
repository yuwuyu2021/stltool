from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs, STEPControl_Reader
from OCP.Interface import Interface_Static
from OCP.IFSelect import IFSelect_ReturnStatus


def write_step(shape, path, schema="AP214IS", write_pcurves=True):
    Interface_Static.SetCVal_s("write.step.schema", schema)
    Interface_Static.SetIVal_s("write.step.surface_curve.mode", 1 if write_pcurves else 0)
    writer = STEPControl_Writer()
    status = writer.Transfer(shape, STEPControl_AsIs)
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        return False, "STEP 写入传输失败 (状态 {})。".format(status)
    Interface_Static.SetIVal_s("write.step.unit", 0)
    writer.Write(path)
    return True, "STEP 文件已写入。"


def read_step(path, schema="AP214IS"):
    """从 STEP 文件读回 shape，用于真实文件再导入预览（所见即所得）。"""
    Interface_Static.SetCVal_s("read.step.schema", schema)
    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        return None
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        return None
    return shape