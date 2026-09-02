from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
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